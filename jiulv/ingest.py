"""收录：扫描目录 → 解析 → 抽元数据 → 分块 → 建全文索引。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import db
from .config import Config
from .extract import chunk_text, extract_meta
from .parsers import SUPPORTED_EXTS, extract_text, parser_status
from .tokenizer import tokenize_joined

HOOK_HEADER_RE = re.compile(r"^##\s+(H-[A-Za-z0-9\u4e00-\u9fff]+-\d+)\s*$", re.M)
HOOK_FIELD_RE = re.compile(r"^[\-\*]\s*\*{0,2}(原文|文本|类型|人群|痛点|机制|转化|来源|状态)\*{0,2}[：:]\s*(.+?)\s*$", re.M)
BUCKETS = {
    "01_开头钩子": "opening",
    "02_收藏钩子": "save",
    "03_评论钩子": "comment",
    "04_失败案例": "fail",
    "05_选题池": "topic",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class IngestReport:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: List[Dict[str, str]] = field(default_factory=list)
    hooks: int = 0
    removed: int = 0
    annotations: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "removed": self.removed,
            "hooks": self.hooks,
            "annotations": self.annotations,
            "failed": self.failed[:30],
            "failed_total": len(self.failed),
            "by_type": self.by_type,
            "duration_s": round(self.duration_s, 2),
        }


def _glob_to_regex(pattern: str):
    """把 glob 翻译成正则：** 跨目录，* 不跨目录，? 单字符。"""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i):
            out.append("(?:/.*)?")
            i += 4
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            char = pattern[i]
            if char == "*":
                out.append("[^/]*")
            elif char == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(char))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _pattern_hits(rel: str, name: str, pattern: str) -> bool:
    """支持 **/*.md、**/_raw/**、*.tmp、目录名；不含 / 的模式只比文件名。"""
    try:
        regex = _compiled(pattern)
    except re.error:
        return False
    return bool(regex.match(rel) or regex.match(name))


@lru_cache(maxsize=256)
def _compiled(pattern: str):
    return _glob_to_regex(pattern)


def _iter_files(root: Path, include: List[str], exclude: List[str]) -> Iterable[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        rel = path.relative_to(root).as_posix()
        if any(_pattern_hits(rel, path.name, pat) for pat in exclude):
            continue
        if not include or any(_pattern_hits(rel, path.name, pat) for pat in include):
            yield path


def iter_source_files(root, include, exclude) -> Iterable[Path]:
    """公开的文件遍历：连接层统计「这个库有多少篇」、同步层算指纹都用它。

    和真正收录时用的是同一套 include/exclude 判定，所以界面上显示的数字
    就是会入库的数字，不会出现「看到 800 篇、入库 600 篇」的对不上。
    """
    return _iter_files(Path(root), list(include or []), list(exclude or []))


def ingest(
    config: Config,
    conn,
    rebuild: bool = False,
    only_source: Optional[str] = None,
    verbose: bool = False,
    prune: bool = True,
    logger=None,
    refresh_derived: Optional[bool] = None,
) -> IngestReport:
    started = datetime.now()
    report = IngestReport()
    if rebuild:
        conn.executescript(
            "DELETE FROM chunks_fts; DELETE FROM chunks; DELETE FROM docs_fts;"
            "DELETE FROM docs; DELETE FROM hooks; DELETE FROM embeddings;"
            "DELETE FROM annotations; DELETE FROM doc_concepts; DELETE FROM concepts;"
        )
        conn.commit()

    sources = config.sources
    if only_source:
        sources = [s for s in sources if s["name"] == only_source]
        if not sources:
            raise SystemExit(f"找不到来源：{only_source}")

    status = parser_status()
    missing = [k for k, ok in status.items() if not ok]
    if missing and logger:
        logger(f"提示：未安装 {', '.join(missing)}，对应格式会被跳过（pip install 'jiulv-kb[parsers]'）")

    for src in sources:
        root = Path(src["path"])
        if not root.exists():
            report.failed.append({"path": str(root), "error": "目录不存在"})
            if logger:
                logger(f"跳过来源 {src['name']}：目录不存在 {root}")
            continue

        conn.execute(
            "INSERT INTO sources(name, group_name, root, last_scan) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET group_name=excluded.group_name, "
            "root=excluded.root, last_scan=excluded.last_scan",
            (src["name"], src["group"], str(root), now_iso()),
        )
        conn.commit()

        seen_paths: List[str] = []
        for path in _iter_files(root, src.get("include", []), src.get("exclude", [])):
            report.scanned += 1
            rel_path = path.relative_to(root).as_posix()
            seen_paths.append(str(path))
            uid = sha1(f"{src['name']}|{rel_path}")
            mtime = path.stat().st_mtime
            row = conn.execute(
                "SELECT id, mtime, content_hash, doc_type FROM docs WHERE uid=?", (uid,)
            ).fetchone()
            # mtime 没变就不解析，增量收录才够快
            if row is not None and abs((row["mtime"] or 0) - mtime) < 1e-6:
                report.skipped += 1
                continue
            try:
                text, fmt = extract_text(path)
            except Exception as exc:  # 解析失败不中断整批
                report.failed.append({"path": str(path), "error": str(exc)})
                if verbose and logger:
                    logger(f"  解析失败 {rel_path}: {exc}")
                continue

            if not text or len(text.strip()) < 20:
                report.skipped += 1
                continue

            content_hash = sha1(text)
            if row is not None and row["content_hash"] == content_hash:
                conn.execute("UPDATE docs SET mtime=? WHERE id=?", (mtime, row["id"]))
                report.skipped += 1
                continue
            decision = "add" if row is None else "update"

            meta = extract_meta(text, rel_path, path.stem)
            payload = {
                "uid": uid,
                "source": src["name"],
                "group_name": src["group"],
                "rel_path": rel_path,
                "abs_path": str(path),
                "ext": path.suffix.lower().lstrip("."),
                "title": meta.title,
                "doc_type": meta.doc_type,
                "grade": meta.grade,
                "region": meta.region,
                "hotel": meta.hotel,
                "brand": meta.brand,
                "author": meta.author,
                "platform": meta.platform,
                "published_at": meta.published_at,
                "url": meta.url,
                "tags": " ".join(meta.tags),
                "metrics_json": json.dumps(meta.metrics, ensure_ascii=False),
                "summary": meta.summary,
                "content": text,
                "char_count": len(text),
                "content_hash": content_hash,
                "mtime": mtime,
                "indexed_at": now_iso(),
            }

            if decision == "add":
                cols = ", ".join(payload)
                placeholders = ", ".join("?" for _ in payload)
                cur = conn.execute(
                    f"INSERT INTO docs ({cols}) VALUES ({placeholders})", tuple(payload.values())
                )
                doc_id = cur.lastrowid
                report.added += 1
            else:
                doc_id = row["id"]
                assigns = ", ".join(f"{k}=?" for k in payload if k != "uid")
                conn.execute(
                    f"UPDATE docs SET {assigns} WHERE id=?",
                    tuple(v for k, v in payload.items() if k != "uid") + (doc_id,),
                )
                conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
                conn.execute("DELETE FROM docs_fts WHERE doc_id=?", (doc_id,))
                conn.execute("DELETE FROM embeddings WHERE chunk_id NOT IN (SELECT id FROM chunks)")
                report.updated += 1

            _index_doc(conn, doc_id, meta.title, text)
            if meta.doc_type == "hook":
                report.hooks += _ingest_hooks(conn, doc_id, rel_path, text, src["group"])
            conn.execute(
                "INSERT INTO docs_fts(doc_id, title_tokens) VALUES(?,?)",
                (doc_id, tokenize_joined(meta.title)),
            )
            report.by_type[meta.doc_type] = report.by_type.get(meta.doc_type, 0) + 1
            if verbose and logger:
                logger(f"  {decision:6s} {meta.doc_type:6s} {meta.title[:40]}")

        if prune:
            report.removed += _prune(conn, src["name"], seen_paths)
        conn.execute(
            "UPDATE sources SET doc_count=(SELECT COUNT(*) FROM docs WHERE source=?) WHERE name=?",
            (src["name"], src["name"]),
        )
        conn.commit()

    conn.execute("INSERT INTO meta(key,value) VALUES('last_ingest',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now_iso(),))
    conn.commit()

    # 注解与概念图都从 docs.content 现算，不读盘也不联网。没有新增/改动时跳过，
    # 否则每轮同步都要白算一遍一万多条注解——监听模式下这是最贵的一步。
    touched = report.added + report.updated + report.removed
    should_refresh = refresh_derived if refresh_derived is not None else (bool(touched) or rebuild)
    if should_refresh:
        from . import annotate as annotate_mod
        from . import graph as graph_mod

        report.annotations = int(annotate_mod.run(conn, logger=logger).get("total", 0))
        graph_mod.build(conn, config, logger=logger)

    report.duration_s = (datetime.now() - started).total_seconds()
    return report


def _index_doc(conn, doc_id: int, title: str, text: str) -> None:
    for seq, (heading, chunk) in enumerate(chunk_text(text)):
        cur = conn.execute(
            "INSERT INTO chunks(doc_id, seq, heading, text, char_count) VALUES(?,?,?,?,?)",
            (doc_id, seq, heading, chunk, len(chunk)),
        )
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, doc_id, heading, tokens) VALUES(?,?,?,?)",
            (cur.lastrowid, doc_id, heading or "", tokenize_joined(heading + "\n" + chunk)),
        )


def _prune(conn, source: str, seen_paths: List[str]) -> int:
    rows = conn.execute("SELECT id, abs_path FROM docs WHERE source=?", (source,)).fetchall()
    seen = set(seen_paths)
    removed = 0
    for row in rows:
        if row["abs_path"] not in seen:
            conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (row["id"],))
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (row["id"],))
            conn.execute("DELETE FROM docs_fts WHERE doc_id=?", (row["id"],))
            conn.execute("DELETE FROM docs WHERE id=?", (row["id"],))
            removed += 1
    return removed


def _ingest_hooks(conn, doc_id: int, rel_path: str, text: str, group: str) -> int:
    """解析「## H-R-001」这种块，落进 hooks 表供精确检索。"""
    bucket = next((v for k, v in BUCKETS.items() if k in rel_path), "opening")
    blocks = list(HOOK_HEADER_RE.finditer(text))
    if not blocks:
        return 0
    saved = 0
    for idx, match in enumerate(blocks):
        start = match.end()
        end = blocks[idx + 1].start() if idx + 1 < len(blocks) else len(text)
        body = text[start:end]
        fields = {m.group(1): m.group(2).strip() for m in HOOK_FIELD_RE.finditer(body)}
        original = fields.get("原文") or fields.get("文本")
        if not original:
            continue
        conn.execute(
            "INSERT INTO hooks(code, doc_id, bucket, text, hook_type, audience, pain, mechanism,"
            " conversion, source, status, region, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET doc_id=excluded.doc_id, bucket=excluded.bucket,"
            " text=excluded.text, hook_type=excluded.hook_type, audience=excluded.audience,"
            " pain=excluded.pain, mechanism=excluded.mechanism, conversion=excluded.conversion,"
            " source=excluded.source, status=excluded.status, updated_at=excluded.updated_at",
            (
                match.group(1),
                doc_id,
                bucket,
                original,
                fields.get("类型", ""),
                fields.get("人群", ""),
                fields.get("痛点", ""),
                fields.get("机制", ""),
                fields.get("转化", ""),
                fields.get("来源", ""),
                fields.get("状态", ""),
                "",
                now_iso(),
            ),
        )
        saved += 1
    return saved
