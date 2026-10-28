"""元数据抽取与分块。

资料库里的 md 大多是半结构化的：开头一行 `> 爆款等级：B级素材 | 编号：笔记132`，
下面一串 `- **标题**：...`。这里用宽松的正则把它们捞出来，捞不到就退回标题和
文件名推断——宁可少填，不要瞎填，因为下游要靠这些字段做筛选和事实核对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 分类

TYPE_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("13_钩子库", "钩子库"), "hook"),
    (("04_风格规则与范文", "规则总库", "写作规则", "风格规则", "ruike_writing_rules", "ruikeke-rules"), "rule"),
    (("03_爆款精选索引", "精选索引", "索引"), "index"),
    (("07_硬资料", "政策", "policy", "官方"), "fact"),
    (("05_代运营", "代运营", "ops-plan", "proposal", "报价", "合同"), "ops"),
    (("06_每日采集产出", "每日采集", "daily"), "daily"),
    (("room-types", "roomtypes", "选房", "房型"), "guide"),
    (("02_地区素材库", "素材库", "viral_posts", "material_export", "bobo_hotel_diary", "素材"), "note"),
]

REGION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "海南": ("海南", "三亚", "陵水", "万宁", "海口", "石梅湾", "亚龙湾", "海棠湾",
             "大东海", "太阳湾", "清水湾", "神州半岛", "日月湾", "博鳌", "儋州", "文昌"),
    "云贵川": ("云贵川", "云南", "丽江", "大理", "腾冲", "西双版纳", "香格里拉", "昆明",
               "泸沽湖", "建水", "弥勒", "抚仙湖", "贵州", "贵阳", "四川", "成都", "九寨"),
    "西安": ("西安", "钟楼", "大雁塔", "大唐不夜城", "城墙", "小寨", "北站"),
    "长白山": ("长白山", "二道白河", "北坡", "西坡", "万达度假区", "鲁能胜地"),
    "新疆": ("新疆", "阿勒泰", "禾木", "喀纳斯", "伊犁", "乌鲁木齐", "吉克普林", "将军山"),
    "崇礼": ("崇礼", "太舞", "云顶", "万龙", "富龙", "太子城"),
    "东南亚": ("东南亚", "普吉", "马尔代夫", "巴厘", "泰国", "清迈", "曼谷", "新加坡",
               "吉隆坡", "越南", "岘港", "日本", "冲绳", "北海道", "沙巴"),
    "上海": ("上海", "外滩", "浦东", "迪士尼"),
    "北京": ("北京", "环球影城", "三里屯"),
    "广深": ("广州", "深圳", "珠海", "香港", "澳门"),
}

GRADE_RE = re.compile(r"(?:爆款等级[：:]\s*)?([SAB])级", re.I)
FIELD_RES = {
    "title": re.compile(r"^[\-\*>\s]*\*{0,2}标题\*{0,2}[：:]\s*(.+?)\s*$", re.M),
    "author": re.compile(r"^[\-\*>\s]*\*{0,2}作者\*{0,2}[：:]\s*(.+?)\s*$", re.M),
    "region": re.compile(r"^[\-\*>\s]*\*{0,2}地区\*{0,2}[：:]\s*(.+?)\s*$", re.M),
    "hotel": re.compile(r"^[\-\*>\s]*\*{0,2}酒店\*{0,2}[：:]\s*(.+?)\s*$", re.M),
    "brand": re.compile(r"^[\-\*>\s]*\*{0,2}brand(?:_type)?\*{0,2}[：:]\s*(.+?)\s*$", re.M | re.I),
    "publish": re.compile(r"^[\-\*>\s]*\*{0,2}发布时间\*{0,2}[：:]\s*(.+?)\s*$", re.M),
    "url": re.compile(r"(https?://[^\s)\]）]+)"),
    "metrics": re.compile(r"^[\-\*>\s]*\*{0,2}互动数据\*{0,2}[：:]\s*(.+?)\s*$", re.M),
}
METRIC_PAIR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万)?\s*(赞|收藏|评论|点赞|阅读|播放|转发|评|藏)")
HEADING_TITLE_RE = re.compile(r"^#{1,3}\s*笔记\s*\d*[：:]\s*(.+?)\s*$", re.M)
FIRST_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


@dataclass
class DocMeta:
    title: str = ""
    doc_type: str = "misc"
    grade: str = ""
    region: str = ""
    hotel: str = ""
    brand: str = ""
    author: str = ""
    platform: str = ""
    published_at: str = ""
    url: str = ""
    tags: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    summary: str = ""


INDEX_NAME_HINTS = ("索引", "总览", "目录", "导航", "readme", "README")


def classify(rel_path: str, text: str) -> str:
    """按目录判断类型。只看目录不看文件名——文件名里的「选房」是内容词，不是分类词。

    例外是索引页：它躺在「02_地区素材库」这类内容目录里，光看目录会被当成一篇笔记，
    然后因为罗列了所有标题，在检索里稳压真正的笔记一头。
    """
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    if any(hint in name for hint in INDEX_NAME_HINTS):
        return "index"
    parts = rel_path.replace("\\", "/").split("/")
    dirs = "/".join(parts[:-1])
    for keywords, doc_type in TYPE_RULES:
        if any(k in dirs for k in keywords):
            return doc_type
    haystack = rel_path.replace("\\", "/")
    head = text[:600]
    if "钩子" in head and "机制" in head:
        return "hook"
    if "规则" in head and ("禁止" in head or "必须" in head):
        return "rule"
    if any(k in haystack for k in ("规则", "手册")) and "规则" in head:
        return "rule"
    return "misc"


def detect_region(text: str, rel_path: str = "") -> str:
    blob = (rel_path.replace("\\", "/") + "\n" + text[:4000])
    best, best_hits = "", 0
    for region, keywords in REGION_KEYWORDS.items():
        hits = sum(blob.count(k) for k in keywords)
        if hits > best_hits:
            best, best_hits = region, hits
    return best if best_hits >= 1 else ""


def parse_metrics(raw: str) -> Dict[str, float]:
    """把「839赞 / 1131收藏 / 33评论」或「146 评论，13 赞 66 收藏」解析成数字。"""
    out: Dict[str, float] = {}
    mapping = {"赞": "likes", "点赞": "likes", "收藏": "saves", "藏": "saves",
               "评论": "comments", "评": "comments",
               "阅读": "views", "播放": "views", "转发": "shares"}
    for num, wan, unit in METRIC_PAIR_RE.findall(raw or ""):
        key = mapping.get(unit)
        if not key:
            continue
        value = float(num) * (10000 if wan else 1)
        out[key] = max(out.get(key, 0), value)
    return out


def _first_line_title(text: str) -> str:
    m = HEADING_TITLE_RE.search(text[:3000])
    if m:
        return m.group(1)
    m = FIRST_H1_RE.search(text[:2000])
    if m:
        return m.group(1)
    for line in text.splitlines():
        line = line.strip().lstrip("#>-* ").strip()
        if len(line) >= 4:
            return line[:80]
    return ""


def _clean_filename(stem: str) -> str:
    name = re.sub(r"^[SABsab]_+", "", stem)
    name = re.sub(r"^(笔记|第)?\d+[_\-\s]*", "", name)
    name = re.sub(r"[，,]?\s*(高价值|高讨论度|高赞|大爆款|\d+\s*评论|\d+\s*赞)+.*$", "", name)
    return name.strip(" _-")[:80]


def extract_meta(text: str, rel_path: str, stem: str) -> DocMeta:
    meta = DocMeta()
    head = text[:6000]
    for key, regex in FIELD_RES.items():
        m = regex.search(head)
        if not m:
            continue
        value = m.group(1).strip()
        if key == "region":
            meta.region = value
        elif key == "publish":
            meta.published_at = value
        elif key == "metrics":
            meta.metrics = parse_metrics(value)
        elif key == "url":
            meta.url = value
        else:
            setattr(meta, key, value)

    # url 单独再捞一次（很多文件只在字段里写了一次链接）
    if not meta.url:
        m = FIELD_RES["url"].search(head)
        if m:
            meta.url = m.group(1)

    # 正文里的碎片化互动数据（每日采集那种「146 评论，13 赞 66 收藏」）
    if not meta.metrics:
        meta.metrics = parse_metrics(head[:2000])

    grade_match = GRADE_RE.search(head) or GRADE_RE.search(rel_path)
    if grade_match:
        meta.grade = grade_match.group(1).upper()

    meta.doc_type = classify(rel_path, text)
    meta.title = meta.title or _first_line_title(text) or _clean_filename(stem)
    # 全量快照这种一个文件装几百篇的，取第一个小标题当标题会张冠李戴，用文件名更准
    if text.count("\n### 笔记") > 3 or text.count("\n## 笔记") > 3:
        meta.title = _clean_filename(stem) or meta.title
    if not meta.region:
        meta.region = detect_region(text, rel_path)

    # 地区字段长这样：海南·三亚·文华东方（大东海/珊瑚湾）
    if meta.region and "·" in meta.region and not meta.hotel:
        parts = [p.strip() for p in re.split(r"[·・]", meta.region) if p.strip()]
        if len(parts) >= 3:
            meta.hotel = re.sub(r"[（(].*?[）)]", "", parts[2]).strip()
    if meta.region:
        meta.region = meta.region.split("·")[0].strip() or meta.region

    if not meta.platform:
        meta.platform = "xiaohongshu"
    meta.summary = make_summary(text)
    meta.tags = build_tags(meta, rel_path)
    return meta


def make_summary(text: str, limit: int = 150) -> str:
    """取第一段像正文的内容做摘要，跳过字段行和标题。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        if re.match(r"^[\-\*]\s*\*{0,2}(标题|作者|地区|发布时间|互动数据|链接|采集日期|编号)", line):
            continue
        if re.match(r"^[\-\*]\s*$", line):
            continue
        cleaned = re.sub(r"[*_`>]", "", line)
        if len(cleaned) >= 12:
            return cleaned[:limit]
    return ""


def build_tags(meta: DocMeta, rel_path: str) -> List[str]:
    tags: List[str] = []
    if meta.grade:
        tags.append(f"{meta.grade}级")
    if meta.hotel:
        tags.append(meta.hotel)
    for part in re.split(r"[/\\]", rel_path):
        part = part.strip()
        if part and re.match(r"^\d{2}_", part):
            tags.append(part)
    if meta.metrics.get("comments", 0) >= 100:
        tags.append("高讨论")
    return list(dict.fromkeys(tags))


FIELD_LINE_RE = re.compile(
    r"^[\-\*>\s|]*\*{0,2}(标题|作者|发布时间|互动|互动数据|地区|酒店|brand|brand_type|品牌|"
    r"采集日期|链接|编号|来源|类型|人群|痛点|机制|转化|状态|原文)\*{0,2}[：:]"
)


def excerpt_for(text: str, terms: List[str], limit: int = 400) -> str:
    """挑一段最能代表正文的段落：跳过字段行和目录行，按命中词打分。

    段落带上所属小标题一起打分，也一起输出——不写标题的片段拿给模型，
    它经常不知道这段话在讲房型还是讲餐饮。
    """
    sections: List[Tuple[str, str]] = []
    heading = ""
    buffer: List[str] = []

    def flush() -> None:
        if buffer:
            sections.append((heading, "\n".join(buffer)))
            buffer.clear()

    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            heading = stripped.lstrip("#").strip()
            continue
        if FIELD_LINE_RE.match(stripped):
            continue
        buffer.append(stripped)
    flush()

    candidates = [(h, s) for h, s in sections if len(re.sub(r"\s", "", s)) >= 30]
    if not candidates:
        candidates = [(h, s) for h, s in sections if s.strip()] or [("", text or "")]

    query_terms = [t for t in (terms or []) if t]

    def score(item: Tuple[str, str]) -> float:
        head, body = item
        hits = sum(body.count(term) for term in query_terms)
        hits += 2 * sum(head.count(term) for term in query_terms)
        # 纯数字/编号堆砌的段落降权，那种内容对写稿没用
        digits = sum(ch.isdigit() for ch in body)
        density = digits / max(len(body), 1)
        return hits * 10 + min(len(body), 600) / 100 - density * 20

    head, body = max(candidates, key=score)
    body = re.sub(r"\s+", " ", body).strip()
    if head and len(head) <= 20 and head not in body:
        body = f"{head}：{body}"
    return body if len(body) <= limit else body[:limit] + "…"


# ---------------------------------------------------------------- 分块

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def chunk_text(text: str, max_chars: int = 700, overlap: int = 80) -> List[Tuple[str, str]]:
    """按标题切段，段落过长再按段落切。返回 [(heading, chunk_text)]。"""
    lines = text.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    current_heading = ""
    buffer: List[str] = []

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            if buffer:
                sections.append((current_heading, buffer))
                buffer = []
            current_heading = match.group(2).strip()
            continue
        buffer.append(line)
    if buffer:
        sections.append((current_heading, buffer))

    chunks: List[Tuple[str, str]] = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        if len(body) <= max_chars:
            chunks.append((heading, body))
            continue
        start = 0
        while start < len(body):
            piece = body[start : start + max_chars]
            if start + max_chars < len(body):
                cut = max(piece.rfind("\n"), piece.rfind("。"), piece.rfind("！"))
                if cut > max_chars * 0.5:
                    piece = piece[: cut + 1]
            piece = piece.strip()
            if piece:
                chunks.append((heading, piece))
            step = max(len(piece) - overlap, 1)
            start += step
    return [c for c in chunks if len(c[1]) >= 20]
