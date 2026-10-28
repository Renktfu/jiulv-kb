"""人群/痛点/钩子类型词表。

默认读包内的 taxonomy.yaml；用户可以在 config.yaml 里用 `taxonomy: 路径` 覆盖，
换成自己团队的习惯叫法。
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")

FALLBACK: Dict[str, Any] = {
    "audience": {},
    "pain": {},
    "hook_types": {},
    "guardrails": [
        "资料里没有的信息一律不写",
        "不写具体价格数字",
        "好和坏都要写，且具体",
    ],
}


@lru_cache(maxsize=4)
def load_taxonomy(path: str | None = None) -> Dict[str, Any]:
    target = Path(path).expanduser() if path else DEFAULT_PATH
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    merged = copy.deepcopy(FALLBACK)
    for key, value in data.items():
        if isinstance(value, dict):
            merged.setdefault(key, {})
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def audience_label(code: str, path: str | None = None) -> str:
    return load_taxonomy(path).get("audience", {}).get(code, {}).get("label", code)


def pain_label(code: str, path: str | None = None) -> str:
    return load_taxonomy(path).get("pain", {}).get(code, {}).get("label", code)
