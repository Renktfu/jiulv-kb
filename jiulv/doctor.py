"""环境自检：一条命令把「为什么跑不起来」需要的信息全打出来。

写这个的起因很实际——Windows 上的故障九成不是逻辑错，是环境错：控制台编码、
长路径没开、可选依赖没装、资料库路径写错、SQLite 被另一个进程占着。
这些各查各的太费劲，不如统一成 `jiulv doctor`，让人把输出整段贴出来就能定位，
AI 拿到这段输出也不用反复追问「你环境是什么版本」。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import Config
from .parsers import parser_status


def _db_info(config: Config) -> Dict[str, Any]:
    path = config.db_path
    info: Dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return info
    try:
        info["size_mb"] = round(path.stat().st_size / 1048576, 1)
        info["too_long"] = len(str(path)) >= 240
    except OSError:
        pass
    try:
        # 只读打开：不建表、不写 WAL，避免和正在跑的 serve 抢锁
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            info["docs"] = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        except sqlite3.DatabaseError:
            info["docs"] = None
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            info["schema_version"] = row[0] if row else ""
        except sqlite3.DatabaseError:
            info["schema_version"] = ""
        try:
            info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        except sqlite3.DatabaseError:
            pass
        conn.close()
        info["readable"] = True
    except sqlite3.Error as exc:
        info["readable"] = False
        info["error"] = str(exc)
    return info


def _source_info(config: Config) -> List[Dict[str, Any]]:
    from .ingest import iter_source_files
    from .platform_compat import path_too_long

    out: List[Dict[str, Any]] = []
    for src in config.sources:
        path = Path(src["path"])
        item: Dict[str, Any] = {
            "name": src["name"],
            "group": src.get("group", ""),
            "path": str(path),
            "exists": path.exists(),
            "too_long": path_too_long(path),
        }
        if path.exists():
            count, longest = 0, 0
            try:
                for file in iter_source_files(
                    path, src.get("include", []), src.get("exclude", [])
                ):
                    count += 1
                    longest = max(longest, len(str(file)))
            except OSError as exc:
                item["error"] = str(exc)
            item["docs"] = count
            # 单个文件的路径长度才是真正会炸的那个数
            item["longest_file_path"] = longest
            item["files_over_limit"] = longest >= 240
        out.append(item)
    return out


def collect(config: Config) -> Dict[str, Any]:
    from . import platform_compat as compat

    env = compat.describe()
    return {
        "jiulv": {
            "version": __version__,
            "package_dir": str(Path(__file__).resolve().parent),
            "cwd": os.getcwd(),
        },
        "env": env,
        "hints": compat.hints(env),
        "config": {
            "path": str(config.path) if config.path else "",
            "base_dir": str(config.base_dir),
            "sources": len(config.sources),
        },
        "parsers": parser_status(),
        "db": _db_info(config),
        "source_items": _source_info(config),
    }


def format_text(data: Dict[str, Any]) -> str:
    """给人看的样子。一行一件事，方便整段复制到对话里。"""
    env = data["env"]
    lines: List[str] = [f"jiulv-kb {data['jiulv']['version']}"]
    lines.append(
        f"系统      {env['platform']} · Python {env['python']} · SQLite {env['sqlite']}"
    )
    lines.append(f"工作目录  {data['jiulv']['cwd']}")
    lines.append(f"中文输出  {'可以' if env.get('unicode_ok') else '不行（见下面第 1 条）'}")
    if env.get("is_windows"):
        enabled = env.get("long_paths_enabled")
        state = {True: "已开启", False: "未开启", None: "查不到"}[enabled]
        lines.append(f"长路径    {state}")

    conf = data["config"]
    lines.append(f"配置文件  {conf['path'] or '（没找到，用默认值）'}")
    lines.append(f"库目录    {conf['base_dir']}")

    db = data["db"]
    if not db["exists"]:
        lines.append("索引库    不存在（还没跑过 sync / ingest）")
    else:
        detail = f"{db.get('size_mb', '?')} MB"
        if db.get("docs") is not None:
            detail += f" · {db['docs']} 篇"
        state = "可读" if db.get("readable") else f"读不了：{db.get('error', '')}"
        lines.append(f"索引库    {db['path']}（{detail}，{state}）")

    missing = [name for name, ok in (data.get("parsers") or {}).items() if not ok]
    lines.append(
        "解析依赖  " + ("都齐了" if not missing else f"缺 {', '.join(missing)}（docx/pdf/xlsx 会被跳过）")
    )

    items = data.get("source_items") or []
    lines.append(f"来源      {len(items)} 个")
    for item in items:
        if not item["exists"]:
            lines.append(f"  [断] {item['name']}  → {item['path']}")
            continue
        flag = "  [路径过长]" if item.get("files_over_limit") else ""
        lines.append(
            f"  [好] {item['name']}  {item.get('docs', 0)} 篇{flag}  → {item['path']}"
        )

    if data.get("hints"):
        lines.append("")
        lines.append("建议：")
        for idx, hint in enumerate(data["hints"], 1):
            lines.append(f"  {idx}. {hint}")
    return "\n".join(lines)
