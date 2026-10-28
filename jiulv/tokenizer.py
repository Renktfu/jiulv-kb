"""中文友好的分词器。

SQLite FTS5 自带的 unicode61 分词器不切中文，trigram 又要求查询至少 3 个字，
「三亚」「瑞吉」这种两字查询会直接空手而归。所以这里自己做一层预处理：

- 连续中文 → 切成二元组（bi-gram）。「三亚文华东方」→ 三亚 亚文 文华 华东 东方
- 单字中文 → 保留单字
- 英文 / 数字 → 整段小写保留，同时把英文按空白和标点断开

建索引和查询都用同一个函数，保证两边词表一致。代价是索引比原文大约 1.6 倍，
换来的是两字词、四字词、长句都能命中，且不需要任何外部分词词典。
"""

from __future__ import annotations

import re
from typing import Iterable, List

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[a-zA-Z0-9][a-zA-Z0-9._+#-]*")

# 检索时忽略的虚词，避免把「的」「了」带进 AND 条件
STOPWORDS = {
    "的", "了", "和", "与", "是", "在", "有", "我", "你", "他", "她", "它",
    "吗", "呢", "吧", "啊", "呀", "就", "都", "也", "还", "把", "被", "给",
    "怎么", "什么", "这个", "那个", "一个", "可以", "the", "a", "an", "of",
    "and", "or", "to", "in", "is", "for",
}


def _cjk_grams(run: str) -> List[str]:
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokenize(text: str, drop_stopwords: bool = False) -> List[str]:
    """把文本切成 token 列表（带位置顺序）。"""
    if not text:
        return []
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(text):
        chunk = match.group(0)
        if re.match(rf"[{_CJK}]", chunk[0]):
            tokens.extend(_cjk_grams(chunk))
        else:
            tokens.append(chunk.lower())
    if drop_stopwords:
        filtered = [t for t in tokens if t not in STOPWORDS]
        # 全被过滤掉时退回原词，避免查询变空
        return filtered or tokens
    return tokens


def tokenize_joined(text: str) -> str:
    """切成空格分隔的 token 串，直接塞进 FTS5 的 unicode61 列。"""
    return " ".join(tokenize(text))


def query_tokens(query: str) -> List[str]:
    """查询侧分词：去掉虚词和重复，保持顺序。"""
    seen = set()
    out = []
    for tok in tokenize(query or "", drop_stopwords=True):
        if len(tok) == 1 and not re.match(rf"[{_CJK}]", tok):
            continue  # 单个英文字母/数字没意义
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def build_match_expr(tokens: Iterable[str], operator: str = "AND") -> str:
    """拼 FTS5 的 MATCH 表达式，全部加引号避免特殊字符报错。"""
    quoted = [f'"{t}"' for t in tokens if t]
    if not quoted:
        return ""
    return f" {operator} ".join(quoted)
