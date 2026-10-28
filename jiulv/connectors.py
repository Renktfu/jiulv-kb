"""连接层：把 WorkBuddy 与 OpenCLaw 两边 agent 的资料库挂进来。

三条设计取舍，写在这里免得后来人反复问：

1. **不拷贝文件。** 两边的资料库都躺在同一台机器的磁盘上，拷一份出来就有版本分叉，
   改完源目录、索引里还是旧的。这里只记路径，收录靠「增量同步」，文件是唯一真相。
2. **不硬编码单一路径。** WorkBuddy 的资料库目录带批次时间戳
   （`~/WorkBuddy/2026-10-16-16-08-58/小红书酒旅资料库`），换一批就失效；
   OpenCLaw 各 agent 的工作区是 `workspace-xhs-*` 这样的兄弟目录，数量会变。
   所以按 glob 探测，多个命中按需取「最近改动的那个」或「每个都连」。
3. **不覆盖手写配置。** 探测结果写进 `sources.yaml`，`config.yaml` 里的 `sources`
   永远优先。想停掉某个自动来源，在 `sources.yaml` 里给它加 `enabled: false`。
"""

from __future__ import annotations

import glob as _glob
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

SOURCES_FILE = "sources.yaml"

DOC_GLOBS = ["**/*.md", "**/*.txt", "**/*.docx", "**/*.pdf", "**/*.xlsx"]

# 系统性噪音：数据目录、打包目录、缓存
NOISE = [
    "**/node_modules/**",
    "**/.git/**",
    "**/.*/**",
    "**/~$*",
    "**/_raw/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
]

# 噪音 + 技术文档：代码仓库里的 agent 规则文件、依赖说明不算酒旅资料
TECH = NOISE + [
    "**/.claude/**",
    "**/.agents/**",
    "**/.codex/**",
    "**/skills/**",
    "**/venv/**",
    "**/.venv/**",
    "**/dist/**",
    "**/build/**",
    "**/site-packages/**",
]


@dataclass
class Profile:
    """一种「库类型」：怎么找、找到几个、收哪些格式。"""

    profile: str
    title: str
    group: str
    globs: List[str]
    select: str = "single"  # single / latest / each
    include: List[str] = field(default_factory=lambda: list(DOC_GLOBS))
    exclude: List[str] = field(default_factory=lambda: list(NOISE))
    deep: bool = False  # 只在 --deep 时才连
    create: bool = False  # 目录不存在就建（回写池）
    name: str = ""  # 来源名模板，支持 {basename}
    hint: str = ""


PROFILES: List[Profile] = [
    Profile(
        profile="wb-library",
        title="WorkBuddy 主资料库",
        group="workbuddy",
        globs=["{wb}/*/小红书酒旅资料库"],
        select="latest",
        hint="每日采集、爆款索引、钩子库、写作规则、地区素材库",
    ),
    Profile(
        profile="wb-output",
        title="WorkBuddy 产出",
        group="workbuddy",
        globs=[
            "{wb}/xhs-bluev-library",
            "{wb}/xhs-jiulv-pipeline",
            "{wb}/xhs-bluev-brands",
        ],
        select="each",
        hint="蓝V 专库与流水线产出的成稿",
    ),
    Profile(
        profile="oc-reference",
        title="OpenCLaw 参考库",
        group="openclaw",
        globs=["{oc}/workspace/reference"],
        select="single",
        hint="瑞克克素材库、房型资料、政策与案例",
    ),
    Profile(
        profile="oc-memory",
        title="OpenCLaw 记忆",
        group="openclaw",
        globs=["{oc}/workspace/memory"],
        select="single",
        hint="agent 自己沉淀的经验与复盘，可当判断依据",
    ),
    Profile(
        profile="oc-xhs",
        title="OpenCLaw 小红书工作区",
        group="openclaw",
        globs=["{oc}/workspace-xhs-*"],
        select="each",
        hint="写手/审稿/交付各 agent 的产出与过程稿",
    ),
    Profile(
        profile="oc-projects",
        title="OpenCLaw 项目库",
        group="openclaw",
        globs=["{oc}/workspace/projects"],
        select="single",
        exclude=TECH,
        deep=True,
        hint="历史项目，夹带代码仓库，只在 --deep 时连",
    ),
    Profile(
        profile="inbox",
        title="Agent 回写",
        group="inbox",
        globs=["{base}/data/inbox"],
        select="single",
        create=True,
        hint="各 agent 通过接口回写的新稿与复盘，构成迭代闭环",
    ),
]

PROFILE_MAP = {p.profile: p for p in PROFILES}


@dataclass
class Candidate:
    profile: str
    title: str
    group: str
    name: str
    path: str
    include: List[str]
    exclude: List[str]
    exists: bool = False
    docs: int = 0
    latest_mtime: float = 0.0
    latest: str = ""
    connected: bool = False  # 已经写进 config.yaml 或 sources.yaml
    enabled: bool = True
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "title": self.title,
            "group": self.group,
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "docs": self.docs,
            "latest": self.latest,
            "connected": self.connected,
            "enabled": self.enabled,
            "deep": PROFILE_MAP[self.profile].deep,
            "reason": self.reason,
        }

    def as_source(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "path": self.path,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "enabled": True,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _resolve(pattern: str, base: Optional[Path] = None) -> str:
    raw = pattern
    if base is not None:
        raw = raw.replace("{base}", str(base))
    raw = raw.replace("{wb}", os.environ.get("JIULV_WB_ROOT", "~/WorkBuddy"))
    raw = raw.replace("{oc}", os.environ.get("JIULV_OC_ROOT", "~/.openclaw"))
    return os.path.expanduser(raw)


def _expand(pattern: str, base: Optional[Path] = None) -> List[Path]:
    """展开 `{base}`（配置目录）、`{wb}`（WorkBuddy 根目录）、`{oc}`（OpenCLaw 根目录）。

    后两个可以用环境变量改，装法不标准的人不用改代码：
        JIULV_WB_ROOT=~/mine/WorkBuddy
        JIULV_OC_ROOT=~/mine/.openclaw
    """
    raw = _resolve(pattern, base)
    if any(ch in raw for ch in "*?["):
        matches = [Path(p) for p in _glob.glob(raw) if Path(p).is_dir()]
    else:
        path = Path(raw)
        matches = [path] if path.exists() else []
    return sorted(set(matches))


def _existing_entries(config) -> List[Dict[str, Any]]:
    """已经登记过的来源：config.yaml 里的 + sources.yaml 里的。

    sources.yaml 要单独再读一遍——同一个进程里刚写完 connect，内存里的 config
    还是旧的，不重读就会把同一批文件再连一次。
    """
    out: List[Dict[str, Any]] = []
    raw_entries = list(config.get("sources", default=[]) or [])
    known_names = {e.get("name") for e in raw_entries if isinstance(e, dict)}
    for entry in _source_entries(config.base_dir):
        if entry.get("name") not in known_names:
            raw_entries.append(entry)
    for src in raw_entries:
        if not isinstance(src, dict) or not src.get("path"):
            continue
        item = dict(src)
        raw = Path(str(item.get("path")))
        item["path"] = str(raw if raw.is_absolute() else (config.base_dir / raw))
        out.append(item)
    return out


def _source_entries(base_dir: Path) -> List[Dict[str, Any]]:
    """只读 sources.yaml，用于判断哪些是生成物。"""
    path = base_dir / SOURCES_FILE
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return list(data.get("sources") or [])


def discover(
    config,
    deep: bool = False,
    with_docs: bool = True,
    create: bool = False,
) -> List[Candidate]:
    """探测本机上真实存在的资料库。不猜、不联网、不改任何文件。

    `create=True` 只给 connect 用：回写池的目录不存在时建出来。查询状态时不建。
    """
    known = _existing_entries(config)
    known_paths = {os.path.realpath(e["path"]) for e in known if e.get("path")}
    known_names = {e.get("name") for e in known if e.get("name")}

    out: List[Candidate] = []
    for prof in PROFILES:
        if prof.deep and not deep:
            continue
        hits: List[Path] = []
        for pattern in prof.globs:
            hits.extend(_expand(pattern, config.base_dir))
            if prof.select != "each" and hits:
                break
        hits = [h for h in dict.fromkeys(hits)]

        if prof.create and not hits and create:
            path = Path(prof.globs[0].replace("{base}", str(config.base_dir)))
            path.mkdir(parents=True, exist_ok=True)
            hits = [path]

        if prof.select == "latest" and len(hits) > 1:
            hits = [max(hits, key=lambda p: _dir_mtime(p))]

        if not hits:
            out.append(
                Candidate(
                    profile=prof.profile,
                    title=prof.title,
                    group=prof.group,
                    name=prof.name or prof.title,
                    path=_resolve(prof.globs[0], config.base_dir),
                    include=prof.include,
                    exclude=prof.exclude,
                    exists=False,
                    reason="目录不存在",
                )
            )
            continue

        for path in hits:
            short = path.name[len("workspace-"):] if path.name.startswith("workspace-") else path.name
            name = prof.name or (
                prof.title
                if prof.select != "each"
                else f"{prof.title}·{short}"
            )
            candidate = Candidate(
                profile=prof.profile,
                title=prof.title,
                group=prof.group,
                name=name,
                path=str(path),
                include=list(prof.include),
                exclude=list(prof.exclude),
                exists=True,
            )
            candidate.connected = os.path.realpath(str(path)) in known_paths or name in known_names
            if with_docs:
                candidate.docs, candidate.latest_mtime = _count_docs(path, prof.include, prof.exclude)
                candidate.latest = (
                    datetime.fromtimestamp(candidate.latest_mtime).strftime("%Y-%m-%d %H:%M")
                    if candidate.latest_mtime
                    else ""
                )
            out.append(candidate)
    return out


def _dir_mtime(path: Path) -> float:
    best = path.stat().st_mtime
    try:
        for child in path.iterdir():
            if child.is_dir():
                best = max(best, child.stat().st_mtime)
    except OSError:
        pass
    return best


def _count_docs(root: Path, include: List[str], exclude: List[str]) -> tuple:
    from .ingest import iter_source_files

    count, latest = 0, 0.0
    for file in iter_source_files(root, include, exclude):
        count += 1
        try:
            latest = max(latest, file.stat().st_mtime)
        except OSError:
            continue
    return count, latest


def plan(config, deep: bool = False, include_connected: bool = False) -> Dict[str, Any]:
    """探测 + 决定要写哪些。不落盘。"""
    candidates = discover(config, deep=deep)
    picked = [
        c for c in candidates
        if c.exists and (include_connected or not c.connected)
    ]
    return {
        "candidates": [c.as_dict() for c in candidates],
        "picked": [c.as_dict() for c in picked],
        "sources": [c.as_source() for c in picked],
        "missing": [c.as_dict() for c in candidates if not c.exists],
    }


def write_sources_file(config, sources: List[Dict[str, Any]]) -> Path:
    """写 sources.yaml。保留旧文件里被手动停用的条目，避免每次 connect 又活过来。"""
    path = config.base_dir / SOURCES_FILE
    previous = _source_entries(config.base_dir)
    disabled = [
        item for item in previous
        if item.get("enabled") is False
        and not any(os.path.realpath(str(item.get("path"))) == os.path.realpath(s["path"]) for s in sources)
    ]
    payload = {
        "generated_at": _now_iso(),
        "sources": list(sources) + disabled,
    }
    header = (
        "# 由 `jiulv connect` 生成，可手改。\n"
        "# 规则：config.yaml 里的 sources 优先级更高；条目加 `enabled: false` 可停用。\n"
    )
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.write_text(header + body, encoding="utf-8")
    return path


def connect(
    config,
    deep: bool = False,
    only: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """探测 → 生成 sources.yaml。返回写入了哪些、跳过了哪些。"""
    candidates = discover(config, deep=deep, create=True)
    picked: List[Candidate] = []
    skipped: List[Dict[str, Any]] = []
    for cand in candidates:
        if not cand.exists:
            skipped.append({**cand.as_dict(), "reason": cand.reason or "目录不存在"})
            continue
        if only and cand.profile not in only:
            skipped.append({**cand.as_dict(), "reason": "未在 --only 里指定"})
            continue
        if cand.connected:
            skipped.append({**cand.as_dict(), "reason": "已连（config.yaml 或 sources.yaml 里已有同路径）"})
            continue
        if cand.docs == 0 and not PROFILE_MAP[cand.profile].create:
            skipped.append({**cand.as_dict(), "reason": "目录里没有可收录的文档格式"})
            continue
        picked.append(cand)

    result: Dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "connected": [c.as_dict() for c in picked],
        "skipped": skipped,
        "docs_added": sum(c.docs for c in picked),
    }
    if dry_run or not picked:
        result["written"] = ""
        return result

    path = write_sources_file(config, [c.as_source() for c in picked])
    result["written"] = str(path)
    return result


def status(config) -> Dict[str, Any]:
    """给界面/接口看的连接状态：每个库在不在、多少篇、上次扫到什么时候。"""
    from .sync import read_state

    state = read_state(config)
    candidates = discover(config, deep=True)
    items = []
    for cand in candidates:
        item = cand.as_dict()
        item["last_scan"] = (state.get("sources") or {}).get(cand.name, {}).get("last_scan", "")
        items.append(item)
    return {
        "items": items,
        "connected": sum(1 for i in items if i["connected"]),
        "exists": sum(1 for i in items if i["exists"]),
        "docs_on_disk": sum(i["docs"] for i in items if i["connected"]),
        "last_sync": state.get("last_sync", ""),
        "generated_at": state.get("generated_at", ""),
    }
