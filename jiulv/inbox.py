"""回写池：让 agent 写出来的东西能回到库里，下一次检索就用得上。

迭代闭环的最后一段。前面几段是「库 → agent」：检索、注解、图谱、上下文包。
这一段是「agent → 库」：任何接进来的 agent（OpenCLaw / WorkBuddy / Codex /
DeepSeek Harness）写完一篇，POST 一段 markdown 过来，落成正常素材、走同一条
解析与注解流水线，下一篇组稿时它已经在参考里了。

落盘格式刻意照着库里的老习惯写（`- **标题**：` 那一套），这样元数据抽取、
等级识别、互动数据解析全都能直接复用，不需要给回写内容开特例。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .config import Config

SOURCE_NAME = "Agent 回写"
INBOX_SUBDIR = Path("data") / "inbox"
DOC_TYPES = ("note", "hook", "rule", "fact", "guide", "ops", "daily", "index")

_SLUG_RE = re.compile(r"[\\/:*?\"<>|\s]+")


def inbox_root(config: Config) -> Path:
    return config.base_dir / INBOX_SUBDIR


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _slug(text: str, limit: int = 40) -> str:
    cleaned = _SLUG_RE.sub("-", (text or "").strip()).strip("-.")
    return (cleaned[:limit] or "untitled")


def _ensure_source(config: Config) -> Dict[str, Any]:
    """回写池始终是一个来源。配置里没有就临时补上，用户不必手动改 yaml。"""
    for src in config.sources:
        if src["name"] == SOURCE_NAME:
            return src
    src = {
        "name": SOURCE_NAME,
        "group": "inbox",
        "path": str(inbox_root(config)),
        "include": ["**/*.md"],
        "exclude": ["**/.*/**"],
    }
    config.data.setdefault("sources", [])
    config.data["sources"] = list(config.data["sources"]) + [
        {"name": SOURCE_NAME, "group": "inbox", "path": src["path"],
         "include": src["include"], "exclude": src["exclude"]}
    ]
    return src


def render(
    title: str,
    content: str,
    *,
    hotel: str = "",
    region: str = "",
    grade: str = "",
    author: str = "",
    agent: str = "",
    metrics: Optional[Dict[str, Any]] = None,
    note: str = "",
) -> str:
    lines: List[str] = [f"# {title}".strip(), ""]
    meta_bits = [b for b in (f"{grade}级素材" if grade else "", f"回写：{agent}" if agent else "") if b]
    if meta_bits:
        lines.append("> " + " | ".join(meta_bits))
        lines.append("")
    fields = [
        ("标题", title),
        ("地区", region),
        ("酒店", hotel),
        ("作者", author),
        ("回写时间", _now().strftime("%Y-%m-%d %H:%M")),
    ]
    if metrics:
        pairs = [
            (metrics.get("likes"), "赞"),
            (metrics.get("saves"), "收藏"),
            (metrics.get("comments"), "评论"),
        ]
        stat = " / ".join(f"{int(v)}{unit}" for v, unit in pairs if v)
        if stat:
            fields.append(("互动数据", stat))
    for key, value in fields:
        if value:
            lines.append(f"- **{key}**：{value}")
    if note:
        lines.append(f"- **备注**：{note}")
    lines.append("")
    lines.append(content.strip())
    lines.append("")
    return "\n".join(lines)


def save(
    config: Config,
    *,
    title: str,
    content: str,
    kind: str = "note",
    hotel: str = "",
    region: str = "",
    grade: str = "",
    author: str = "",
    agent: str = "",
    metrics: Optional[Dict[str, Any]] = None,
    note: str = "",
    reindex: bool = True,
) -> Dict[str, Any]:
    """写进回写池并立刻收录。返回 uid 与落盘路径。"""
    kind = kind if kind in DOC_TYPES else "note"
    title = (title or "").strip() or "未命名回写"
    if not (content or "").strip():
        raise ValueError("content 不能为空")

    root = inbox_root(config) / f"inbox_{kind}" / _now().strftime("%Y-%m-%d")
    root.mkdir(parents=True, exist_ok=True)
    # 同名多篇不覆盖：退到带时间戳的文件名
    target = root / f"{_slug(title)}.md"
    if target.exists():
        target = root / f"{_slug(title)}-{_now().strftime('%H%M%S')}.md"

    target.write_text(
        render(
            title, content, hotel=hotel, region=region, grade=grade,
            author=author, agent=agent, metrics=metrics, note=note,
        ),
        encoding="utf-8",
    )

    source = _ensure_source(config)
    result: Dict[str, Any] = {
        "ok": True,
        "path": str(target),
        "source": source["name"],
        "kind": kind,
        "reindexed": False,
        "uid": "",
    }
    if not reindex:
        return result

    from . import sync as sync_mod
    from .ingest import ingest

    conn = db.open_db(config.db_path)
    try:
        report = ingest(config, conn, only_source=SOURCE_NAME, prune=True, verbose=False)
        row = conn.execute("SELECT uid FROM docs WHERE abs_path=?", (str(target),)).fetchone()
        result["uid"] = row["uid"] if row else ""
        result["reindexed"] = True
        result["annotations"] = report.annotations
        result["report"] = {
            "added": report.added,
            "updated": report.updated,
            "removed": report.removed,
            "duration_s": round(report.duration_s, 2),
        }
    finally:
        conn.close()
    # 补记指纹，免得下一轮 sync 误判「没变化」
    sync_mod.record(config, [source])
    return result


def recent(config: Config, limit: int = 20) -> List[Dict[str, Any]]:
    path = inbox_root(config)
    if not path.exists():
        return []
    files = sorted(
        (p for p in path.rglob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, 200))]
    return [
        {
            "path": str(p),
            "title": p.stem,
            "kind": p.parent.parent.name.replace("inbox_", ""),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "bytes": p.stat().st_size,
        }
        for p in files
    ]
