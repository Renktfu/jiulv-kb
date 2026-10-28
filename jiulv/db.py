"""SQLite 存储层：建表、连接、统计。

一张人看的表 + 两张全文索引表 + 一张向量表，就这么点东西。
不需要 Postgres，不需要 es，单个 .sqlite3 文件拷走就能用。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = 2

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 收录来源
CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    group_name  TEXT,
    root        TEXT,
    doc_count   INTEGER DEFAULT 0,
    last_scan   TEXT
);

-- 文档主表
CREATE TABLE IF NOT EXISTS docs (
    id           INTEGER PRIMARY KEY,
    uid          TEXT UNIQUE NOT NULL,       -- 稳定 id：来源+相对路径的哈希
    source       TEXT NOT NULL,
    group_name   TEXT,
    rel_path     TEXT,
    abs_path     TEXT,
    ext          TEXT,
    title        TEXT,
    doc_type     TEXT,                       -- rule / hook / note / index / fact / ops / daily / misc
    grade        TEXT,                       -- S / A / B / ''
    region       TEXT,
    hotel        TEXT,
    brand        TEXT,
    author       TEXT,
    platform     TEXT,
    published_at TEXT,
    url          TEXT,
    tags         TEXT,                       -- 空格分隔
    metrics_json TEXT,                       -- {"likes":1,"saves":2,"comments":3}
    summary      TEXT,
    content      TEXT,
    char_count   INTEGER,
    content_hash TEXT,
    mtime        REAL,
    indexed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_docs_type   ON docs(doc_type);
CREATE INDEX IF NOT EXISTS idx_docs_region ON docs(region);
CREATE INDEX IF NOT EXISTS idx_docs_hotel  ON docs(hotel);
CREATE INDEX IF NOT EXISTS idx_docs_source ON docs(source);
CREATE INDEX IF NOT EXISTS idx_docs_hash   ON docs(content_hash);

-- 标题索引：只存标题的二元分词。正文在 chunks_fts 里，别重复存一遍
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    doc_id UNINDEXED,
    title_tokens,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 段落块：检索命中到段落，再把段落所在的整篇文档返回
CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY,
    doc_id    INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    seq       INTEGER,
    heading   TEXT,
    text      TEXT,
    char_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    heading,
    tokens,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 结构化钩子（从钩子库解析出来，可按类型/人群/痛点精确取）
CREATE TABLE IF NOT EXISTS hooks (
    id         INTEGER PRIMARY KEY,
    code       TEXT UNIQUE,
    doc_id     INTEGER,
    bucket     TEXT,        -- opening / save / comment / fail / topic
    text       TEXT,
    hook_type  TEXT,
    audience   TEXT,
    pain       TEXT,
    mechanism  TEXT,
    conversion TEXT,
    source     TEXT,
    status     TEXT,
    region     TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hooks_type ON hooks(hook_type);

-- 段落向量（语义检索用）
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER NOT NULL,
    model    TEXT NOT NULL,
    dim      INTEGER,
    vec      BLOB,
    PRIMARY KEY (chunk_id, model)
);

-- 发布后的数据回收
CREATE TABLE IF NOT EXISTS outcomes (
    id         INTEGER PRIMARY KEY,
    kind       TEXT,        -- compose / doc
    ref        TEXT,
    title      TEXT,
    hotel      TEXT,
    region     TEXT,
    hook_type  TEXT,
    audience   TEXT,
    views      INTEGER DEFAULT 0,
    likes      INTEGER DEFAULT 0,
    saves      INTEGER DEFAULT 0,
    comments   INTEGER DEFAULT 0,
    follows    INTEGER DEFAULT 0,
    is_hit     INTEGER,
    note       TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_created ON outcomes(created_at);

-- 组稿记录（方便回收时对上是哪次组稿）
CREATE TABLE IF NOT EXISTS compose_log (
    id         TEXT PRIMARY KEY,
    brief_json TEXT,
    created_at TEXT
);

-- ---------------------------------------------------------------- 注解
--
-- 一条注解 = 关于「这篇为什么这么写」的一个判断。三种来源分工明确：
--   origin='source'  语料原文里本来就有的（预订启示、提取要点、采集点评）
--   origin='derived' 由指标或标题按规则推出来的（藏赞比、标题手法）
-- 原文类必须带 evidence 引用，衍生类在文案里自己说明是推算，不混淆。

CREATE TABLE IF NOT EXISTS annotations (
    id       INTEGER PRIMARY KEY,
    doc_id   INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,   -- takeaway/booking/pro/con/voice/structure/core/visual/note/review/signal/title
    label    TEXT,            -- 卡片标题
    text     TEXT NOT NULL,   -- 注解正文
    evidence TEXT,            -- 原文引用，可空
    origin   TEXT,            -- source / derived
    weight   REAL DEFAULT 0.5,
    seq      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ann_doc  ON annotations(doc_id);
CREATE INDEX IF NOT EXISTS idx_ann_kind ON annotations(kind);

-- 概念节点：Obsidian 里除了笔记还有概念，这里同样一等公民对待
CREATE TABLE IF NOT EXISTS concepts (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,   -- region/hotel/author/series/audience/pain/hook/technique
    key       TEXT NOT NULL,
    label     TEXT NOT NULL,
    note      TEXT,            -- 概念说明
    doc_count INTEGER DEFAULT 0,
    UNIQUE(kind, key)
);
CREATE INDEX IF NOT EXISTS idx_concepts_kind ON concepts(kind);

-- 笔记 ↔ 概念：双向链接就靠这张表，正查反查都走它
CREATE TABLE IF NOT EXISTS doc_concepts (
    doc_id     INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    weight     REAL DEFAULT 1.0,
    evidence   TEXT,
    PRIMARY KEY(doc_id, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_dc_concept ON doc_concepts(concept_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    init_db(conn)
    return conn


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    def scalar(sql: str, default: Any = 0) -> Any:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default

    by_type = {
        row["doc_type"] or "misc": row["n"]
        for row in conn.execute(
            "SELECT doc_type, COUNT(*) AS n FROM docs GROUP BY doc_type ORDER BY n DESC"
        )
    }
    by_region = [
        {"region": row["region"], "count": row["n"]}
        for row in conn.execute(
            "SELECT region, COUNT(*) AS n FROM docs WHERE region IS NOT NULL AND region<>'' "
            "GROUP BY region ORDER BY n DESC LIMIT 20"
        )
    ]
    by_source = [
        {"source": row["source"], "count": row["n"]}
        for row in conn.execute(
            "SELECT source, COUNT(*) AS n FROM docs GROUP BY source ORDER BY n DESC"
        )
    ]
    return {
        "docs": scalar("SELECT COUNT(*) FROM docs"),
        "chunks": scalar("SELECT COUNT(*) FROM chunks"),
        "hooks": scalar("SELECT COUNT(*) FROM hooks"),
        "annotations": scalar("SELECT COUNT(*) FROM annotations"),
        "annotated_docs": scalar("SELECT COUNT(DISTINCT doc_id) FROM annotations"),
        "concepts": scalar("SELECT COUNT(*) FROM concepts"),
        "links": scalar("SELECT COUNT(*) FROM doc_concepts"),
        "embedded_chunks": scalar("SELECT COUNT(*) FROM embeddings"),
        "composes": scalar("SELECT COUNT(*) FROM compose_log"),
        "outcomes": scalar("SELECT COUNT(*) FROM outcomes"),
        "chars": scalar("SELECT SUM(char_count) FROM docs"),
        "by_type": by_type,
        "by_region": by_region,
        "by_source": by_source,
        "indexed_at": scalar("SELECT MAX(indexed_at) FROM docs", None),
        "schema_version": scalar("SELECT value FROM meta WHERE key='schema_version'", None),
    }


def doc_row_to_dict(row: sqlite3.Row, with_content: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "uid": row["uid"],
        "source": row["source"],
        "group": row["group_name"],
        "path": row["rel_path"],
        "ext": row["ext"],
        "title": row["title"],
        "doc_type": row["doc_type"],
        "grade": row["grade"],
        "region": row["region"],
        "hotel": row["hotel"],
        "brand": row["brand"],
        "author": row["author"],
        "platform": row["platform"],
        "published_at": row["published_at"],
        "url": row["url"],
        "tags": (row["tags"] or "").split(),
        "metrics": _load_json(row["metrics_json"]),
        "summary": row["summary"],
        "char_count": row["char_count"],
    }
    if with_content:
        out["content"] = row["content"]
    return out


def _load_json(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    import json

    try:
        return json.loads(raw)
    except Exception:
        return {}


ALLOWED_DOC_TYPES: List[str] = [
    "note", "rule", "hook", "index", "fact", "ops", "daily", "guide", "misc",
]
