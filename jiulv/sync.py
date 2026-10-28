"""增量同步：让索引跟上两个资料库的日常产出。

`jiulv ingest` 是「跑一次」，`jiulv sync` 是「该跑才跑」，`jiulv watch` 是「一直盯着」。

判断该不该跑，只看每个来源的（文件数, 总字节, 最新 mtime）三元组——也就是指纹。
指纹没变就整段跳过，连数据库都不开；变了才进 ingest，而 ingest 内部还会按单文件
mtime 二次过滤，所以一次同步的真实成本通常是「几百次 stat + 几篇解析」。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .config import Config
from .platform_compat import atomic_write_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def state_path(config: Config) -> Path:
    return config.db_path.parent / "sync_state.json"


def read_state(config: Config) -> Dict[str, Any]:
    path = state_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(config: Config, state: Dict[str, Any]) -> None:
    """原子写。同时跑着 serve 和 watch 时，两个进程都会写这个文件——
    直接覆盖有机会读到半个 JSON，下一轮就当成「没同步过」，白扫一遍全库。"""
    atomic_write_text(
        state_path(config), json.dumps(state, ensure_ascii=False, indent=2)
    )


def fingerprint(config: Config, sources: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """每个来源一个指纹。数字全用 int，避免浮点比较的抖动。"""
    from .ingest import iter_source_files

    out: Dict[str, Any] = {}
    for src in sources or config.sources:
        root = Path(src["path"])
        if not root.exists():
            out[src["name"]] = {"files": -1, "bytes": -1, "latest": -1}
            continue
        files = 0
        total = 0
        latest = 0.0
        for file in iter_source_files(root, src.get("include", []), src.get("exclude", [])):
            files += 1
            try:
                stat = file.stat()
            except OSError:
                continue
            total += stat.st_size
            latest = max(latest, stat.st_mtime)
        out[src["name"]] = {
            "files": files,
            "bytes": total,
            "latest": int(latest),
        }
    return out


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    changed: List[str] = []
    for name, fp in new.items():
        if old.get(name) != fp:
            changed.append(name)
    for name in old:
        if name not in new:
            changed.append(name)
    return changed


def record(config: Config, sources: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """收录完某个来源后补记指纹。

    回写接口是「存文件 + 直接收录」，不经过 sync_once。如果不补记指纹，
    下次 sync 会发现指纹和上次一样，误判「没变化」而跳过——库和状态就对不上了。
    """
    state = read_state(config)
    current = fingerprint(config, sources)
    state["sources"] = {**(state.get("sources") or {}), **current}
    state["recorded_at"] = _now_iso()
    write_state(config, state)
    return current


def sync_once(
    config: Config,
    force: bool = False,
    only_source: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """指纹变了才收录。返回本次做了什么。"""
    from .ingest import ingest

    sources = config.sources
    if only_source:
        sources = [s for s in sources if s["name"] == only_source]
        if not sources:
            return {"ok": False, "error": f"找不到来源：{only_source}"}

    started = time.time()
    current = fingerprint(config, sources)
    state = read_state(config)
    previous = state.get("sources") or {}
    changed = (
        [name for name, fp in current.items() if previous.get(name) != fp]
        if only_source
        else diff(previous, current)
    )
    changed = [c for c in changed if c in current]

    if not force and not changed:
        return {
            "ok": True,
            "skipped": True,
            "reason": "指纹没变，跳过",
            "changed": [],
            "checked_in_s": round(time.time() - started, 2),
            "last_sync": state.get("last_sync", ""),
        }

    conn = db.open_db(config.db_path)
    try:
        report = ingest(
            config, conn,
            only_source=only_source,
            prune=True,
            verbose=False,
            logger=logger,
        )
    finally:
        conn.close()

    after = read_state(config)
    merged = dict(previous) if only_source else {}
    merged.update(current)
    history: List[Dict[str, Any]] = list(after.get("history") or [])
    entry = {
        "at": _now_iso(),
        "changed": changed,
        "added": report.added,
        "updated": report.updated,
        "removed": report.removed,
        "annotations": report.annotations,
        "duration_s": round(report.duration_s, 2),
    }
    history = (history + [entry])[-20:]

    new_state = {
        "last_sync": _now_iso(),
        "sources": merged,
        "history": history,
    }
    write_state(config, new_state)

    return {
        "ok": True,
        "skipped": False,
        "changed": changed,
        "report": report.as_dict(),
        "duration_s": round(time.time() - started, 2),
        "last_sync": new_state["last_sync"],
    }


def _reload_config(config: Config, say) -> Config:
    """每轮重读一次配置，让新接的来源不用重启就生效。

    加来源最顺手的做法是跑 `jiulv connect` 或直接改 sources.yaml，而常驻进程
    如果只在启动时读一次盘，新目录要等到重启才会被扫到。用户看到的是
    「我明明接上了，怎么还是没进来」，然后开始怀疑入库坏了。
    """
    before = [s["name"] for s in config.sources]
    try:
        config.reload()
    except Exception as exc:  # 手改坏了 YAML 也不能让监听到此为止
        say(f"[{_now_iso()}] 重读配置失败，继续用上一版：{exc}")
        return config

    after = [s["name"] for s in config.sources]
    if after != before:
        say(f"[{_now_iso()}] 配置变了：{len(before)} → {len(after)} 个来源")
        for name in after:
            if name not in before:
                say(f"  + {name}")
        for name in before:
            if name not in after:
                say(f"  - {name}")
    return config


def watch(
    config: Config,
    interval: Optional[int] = None,
    logger=None,
    max_rounds: Optional[int] = None,
    reload: bool = True,
) -> None:
    """长期驻留：隔一会儿看一眼，变了就收录。Ctrl-C 退出。

    `interval=None` 表示跟着配置走：每轮重读 `connect.watch_interval`，
    改了配置下轮就按新间隔跑，不用重启。显式传数字则以传入的为准。
    """
    say = logger or (lambda msg: None)
    rounds = 0

    def _interval(cfg: Config) -> int:
        return max(5, int(interval or cfg.get("connect", "watch_interval", default=120)))

    say(f"开始监听 {len(config.sources)} 个来源，间隔 {_interval(config)}s")
    try:
        while True:
            rounds += 1
            if reload:
                config = _reload_config(config, say)
            try:
                result = sync_once(config, logger=logger)
                if result.get("skipped"):
                    say(f"[{_now_iso()}] 无变化")
                else:
                    report = result.get("report") or {}
                    say(
                        f"[{_now_iso()}] 已同步 {result.get('changed')}："
                        f"+{report.get('added', 0)} ~{report.get('updated', 0)} "
                        f"-{report.get('removed', 0)}（{result.get('duration_s')}s）"
                    )
            except Exception as exc:  # 单轮失败不退出，下一轮再试
                say(f"[{_now_iso()}] 同步失败：{exc}")
            if max_rounds and rounds >= max_rounds:
                return
            time.sleep(_interval(config))
    except KeyboardInterrupt:
        say("已停止监听")
