"""配置加载：config.yaml → dict，带默认值合并与路径展开。"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .platform_compat import path_key

DEFAULTS: Dict[str, Any] = {
    "app": {
        "name": "酒旅爆款知识库",
        "database": "data/kb.sqlite3",
        "host": "127.0.0.1",
        "port": 8787,
    },
    "sources": [],
    "connect": {
        # jiulv watch 的默认轮询间隔（秒）。指纹没变就整轮跳过，调小也不贵
        "watch_interval": 120,
    },
    "search": {
        "max_limit": 50,
        "fallback_to_or": True,
        "title_weight": 6.0,
        "snippet_chars": 220,
    },
    "semantic": {
        "enabled": False,
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "JIULV_EMBED_API_KEY",
        "model": "text-embedding-3-small",
        "batch_size": 32,
        "weight": 0.45,
    },
    "generation": {
        "enabled": False,
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "JIULV_LLM_API_KEY",
        "model": "deepseek-chat",
        "temperature": 0.85,
        "max_tokens": 1600,
    },
    "security": {
        "api_keys": [],
        "allow_localhost_without_key": True,
        "cors_origins": ["*"],
    },
    "compose": {
        "max_references": 6,
        "excerpt_chars": 600,
        "default_style": "瑞克克",
    },
}

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        expanded = _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
        return os.path.expanduser(expanded)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


class Config:
    def __init__(self, data: Dict[str, Any], base_dir: Path, path: Path | None = None):
        self.data = data
        self.base_dir = base_dir
        self.path = path
    # --- 便捷访问 ---
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def db_path(self) -> Path:
        raw = self.get("app", "database", default="data/kb.sqlite3")
        p = Path(raw)
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def sources(self) -> List[Dict[str, Any]]:
        out = []
        for src in self.get("sources", default=[]) or []:
            if not src.get("path"):
                continue
            path = Path(src["path"])
            if not path.is_absolute():
                path = self.base_dir / path
            item = dict(src)
            item["path"] = str(path)
            item.setdefault("group", "default")
            item.setdefault(
                "include", ["**/*.md", "**/*.txt", "**/*.docx", "**/*.pdf", "**/*.xlsx"]
            )
            item.setdefault("exclude", [])
            out.append(item)
        return out


def _load_generated_sources(base_dir: Path) -> List[Dict[str, Any]]:
    """读 `jiulv connect` 生成的 sources.yaml。"""
    path = base_dir / "sources.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return [s for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("path")]


def _merge_sources(manual: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """手写配置优先。同路径或同名的自动来源直接丢弃，避免同一批文件被收两遍。

    同一性用 path_key 判断，不是字符串：Windows 上 `C:\\Data\\KB` 与 `c:/data/kb/`
    是同一个目录，直接比字符串会漏判。
    """
    out: List[Dict[str, Any]] = []
    seen_paths, seen_names = set(), set()
    for src in list(manual) + list(extra):
        if src.get("enabled") is False:
            continue
        name = src.get("name")
        real = path_key(src.get("path") or "")
        if name in seen_names or real in seen_paths:
            continue
        seen_names.add(name)
        seen_paths.add(real)
        out.append(src)
    return out


def load_config(path: str | os.PathLike | None = None) -> Config:
    """加载配置。找不到文件时返回全默认值（此时 sources 为空）。"""
    base_dir = Path.cwd()
    resolved: Path | None = None
    if path:
        resolved = Path(path).expanduser().resolve()
    else:
        for candidate in ("config.yaml", "config.yml"):
            p = Path.cwd() / candidate
            if p.exists():
                resolved = p.resolve()
                break

    data: Dict[str, Any] = {}
    if resolved and resolved.exists():
        with open(resolved, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        base_dir = resolved.parent

    merged = _expand(_merge(DEFAULTS, data))
    base = Path(base_dir)
    merged["sources"] = _merge_sources(
        merged.get("sources") or [],
        [_expand(item) for item in _load_generated_sources(base)],
    )
    return Config(merged, base_dir=base, path=resolved)
