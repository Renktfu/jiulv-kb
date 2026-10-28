"""SQLite 存储层：建表、连接、统计。

一张人看的表 + 两张全文索引表 + 一张向量表，就这么点东西。
不需要 Postgres，不需要 es，单个 .sqlite3 文件拷走就能用。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = 1

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
