"""注解：把每篇里「为什么值得学」的部分拆成独立卡片。

这里不生成、不润色、不猜。三种来源，分工写死在 schema 的 origin 字段里：

1. ``source`` —— 笔记自身的 ``####`` 小节（预订启示 / 提取要点 / 优点 / 缺点避坑 /
   评论区 / 房型分析）。写资料的人当时就把结论写在文末了，只是没人把它当结构化数据。
2. ``source`` —— 每日采集记录里每条后面的长点评（``——`` 之后那段）。这段直接讲
   「这篇的价值在哪、哪儿不能当事实用」，是全库里信息密度最高的注解。
3. ``derived`` —— 由赞藏评比例、标题用词按规则算出来的推断。这类会自己在文案里
   说明是推算，不和原文混在一起。

每条注解都带 evidence 和出处，前端点得回原文对账。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

ANN_KINDS = {
    "takeaway": "写法要点",
    "booking": "预订启示",
    "pro": "可写的卖点",
    "con": "必须写的保留意见",
    "voice": "评论区真话",
    "structure": "房型结构",
    "core": "内容骨架",
    "visual": "视频画面",
    "note": "备注",
    "review": "采集点评",
    "signal": "数据信号",
    "title": "标题手法",
}

# 小节标题 → (kind, 卡片标签, 基础权重)。顺序即优先级，先匹到的赢。
SECTION_MAP: Tuple[Tuple[Tuple[str, ...], str, str, float], ...] = (
    (("预订启示", "预订建议", "预订提示", "订房启示", "旅居启示", "预订建议"), "booking", "预订启示", 0.95),
    (("提取要点", "写作要点", "写作启示"), "takeaway", "提取要点", 1.0),
    (("缺点", "避坑", "槽点", "雷点"), "con", "必须写的保留意见", 1.0),
    (("优点", "优势", "亮点"), "pro", "可写的卖点", 0.75),
    (("评论区", "评论"), "voice", "评论区真话", 0.65),
    (("房型", "选房"), "structure", "房型结构", 0.8),
    (("核心内容", "笔记内容", "核心"), "core", "内容骨架", 0.55),
    (("视频画面", "画面节点"), "visual", "视频画面", 0.45),
    (("备注",), "note", "备注", 0.4),
)

# 文末那几条「写文案时：……」是整篇最贵的，单独抬权重
WRITE_HINT_RE = re.compile(r"^(写文案时|怎么写|写作时|写法上|文案上|写法建议|落笔)[：:]\s*")

HEADING_RE = re.compile(r"^(#{2,4})\s*(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s+(.+?)\s*$")
QUOTE_RE = re.compile(r"[“\"「]([^”\"」\n]{6,90})[”\"」]")
URL_RE = re.compile(r"https?://\S+")
# 笔记抬头的字段行（- **标题**：xxx）。这些是元数据不是内容，
# 混进注解会把「房型结构」灌成一堆 brand_type 和采集日期。
FIELD_NAMES = (
    "标题|作者|发布时间|发布|互动数据|地区|类型|标签|链接|来源|编号|笔记编号|"
    "爆款等级|采集日期|涉及酒店|品牌|推荐指数|字数|图片|视频|平台|"
    "brand_type|type|region|hotel|author|level|grade|url|date|likes|saves|comments|title"
)
FIELD_LINE_RE = re.compile(rf"^\s*[-*>]?\s*\*{{0,2}}({FIELD_NAMES})\*{{0,2}}\s*[：:]", re.I)

# 采集记录里的一条：1. S_长白山_万达锦华套餐加价维权（146 评论，13 赞 66 收藏）——点评……
# 不用单条正则：标题里常自带括号（「长白山-小张（攻略王）」），
# 一次匹配会把括号切错。改成先按 —— 切行，再从两头取指标括号。
DAILY_HEAD_RE = re.compile(r"^\s*\d+\.\s*\*{0,2}([SAB])[_\s]*(.+?)\*{0,2}\s*$")

# 每类注解的条数上限。评论区咨询动辄十几条，不限量就会把「写文案时」
# 和「必须写的保留意见」压到面板最底下 —— 那种注解等于没写。
KIND_QUOTA = {
    "voice": 3,
    "core": 3,
    "structure": 5,
    "pro": 4,
    "con": 5,
    "booking": 5,
    "visual": 3,
    "note": 2,
}


@dataclass
class Ann:
    kind: str
    label: str
    text: str
    evidence: str = ""
    origin: str = "source"
    weight: float = 0.5
    seq: int = 0


def _norm(text: str) -> str:
    """归一化标题：去空格、标点、等级前缀，只留字。"""
    text = re.sub(r"^(?:[SAB])[_\s]+", "", (text or "").strip())
    return re.sub(r"[\s\-_、，,。.！!？?（）()「」“”\"'·/|:：;；\[\]【】]+", "", text)


def _clean(text: str) -> str:
    text = URL_RE.sub("", text or "")
    text = re.sub(r"\*\*|__", "", text)
    return re.sub(r"\s+", " ", text).strip(" -—:：·")


def parse_sections(text: str) -> List[Tuple[str, str]]:
    """按 ### / #### 切小节，返回 [(标题, 段正文)]。"""
    out: List[Tuple[str, str]] = []
    heading = ""
    buf: List[str] = []

    def flush() -> None:
        if heading and buf:
            body = "\n".join(buf).strip()
            if body:
                out.append((heading, body))

    for line in (text or "").splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush()
            heading = m.group(2).strip()
            buf = []
            continue
        buf.append(line)
    flush()
    return out


def _kind_of(heading: str) -> Optional[Tuple[str, str, float]]:
    for keys, kind, label, weight in SECTION_MAP:
        if any(k in heading for k in keys):
            return kind, label, weight
    return None


def _split_items(body: str) -> List[str]:
    """段内按 bullet 拆条；没有 bullet 就把整段当一条。"""
    items = [BULLET_RE.match(line).group(1) for line in body.splitlines() if BULLET_RE.match(line)]
    if not items:
        items = [line.strip() for line in body.splitlines() if line.strip()]
    return items


def annotations_from_sections(text: str) -> List[Ann]:
    anns: List[Ann] = []
    seq = 0
    used: Dict[str, int] = {}
    for heading, body in parse_sections(text):
        mapped = _kind_of(heading)
        if not mapped:
            continue
        kind, label, weight = mapped
        items = _split_items(body)
        kept = 0
        for raw in items:
            item = _clean(raw)
            if len(item) < 8:
                continue
            if FIELD_LINE_RE.match(raw):
                continue
            # 条目太多说明这段在堆料，后半段降权
            w = weight if kept < 6 else weight * 0.75
            item_kind, item_label = kind, label
            hint = WRITE_HINT_RE.match(item)
            if hint:
                item = _clean(item[hint.end():])
                if len(item) < 8:
                    continue
                item_kind, item_label, w = "takeaway", "写文案时", 1.3
            quota = KIND_QUOTA.get(item_kind)
            if quota is not None and used.get(item_kind, 0) >= quota:
                continue
            used[item_kind] = used.get(item_kind, 0) + 1
            quote = QUOTE_RE.search(raw)
            seq += 1
            anns.append(
                Ann(
                    kind=item_kind,
                    label=item_label,
                    text=item,
                    evidence=f"原文引用：{quote.group(1)}" if quote else "",
                    origin="source",
                    weight=w,
                    seq=seq,
                )
            )
            kept += 1
    return anns


# ---------------------------------------------------------------- 采集点评


def parse_daily_reviews(text: str) -> List[Dict[str, str]]:
    """从一份采集记录里抽出每条笔记后面的长点评。"""
    out: List[Dict[str, str]] = []
    for line in (text or "").splitlines():
        if "——" not in line:
            continue
        head, _, tail = line.partition("——")
        m = DAILY_HEAD_RE.match(head)
        if not m:
            continue
        grade, rest = m.group(1), m.group(2).strip()
        # 指标括号从第一个（ 取到最后一个 ），中间嵌套的括号一并算进去
        start, end = rest.find("（"), rest.rfind("）")
        if -1 < start < end:
            hint, metrics = rest[:start], rest[start + 1 : end]
        else:
            hint, metrics = rest, ""
        hint = hint.strip(" *")
        review = _clean(tail)
        if len(review) < 20 or len(hint) < 4:
            continue
        out.append(
            {
                "grade": grade,
                "hint": hint,
                "metrics": _clean(metrics),
                "review": review,
            }
        )
    return out


def build_review_index(daily_texts: List[str]) -> Dict[str, Dict[str, str]]:
    """把全库采集记录摊平成 归一化标题 → 点评 的索引。

    采集记录里写的名字和落库文件名是同一套（等级_地区_标题），所以能对上。
    """
    index: Dict[str, Dict[str, str]] = {}
    for text in daily_texts:
        for item in parse_daily_reviews(text):
            key = _norm(item["hint"])
            if len(key) < 6:
                continue
            index.setdefault(key, item)
    return index


def match_review(candidates: List[str], index: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """先精确、再包含。候选里通常同时有文内标题和文件名 stem —— 采集记录
    写的往往是文件名（S_长白山_万达锦华套餐加价维权），文内标题却是另一套。"""
    keys = [k for k in (_norm(c) for c in candidates) if k]
    for key in keys:
        hit = index.get(key)
        if hit:
            return hit
    for key in keys:
        # 记录里常把标题截短，用包含关系兜一层，短的也得够长才算
        for k, item in index.items():
            if len(k) >= 8 and (k in key or key in k):
                return item
    return None


# ---------------------------------------------------------------- 指标与标题


ACTION_HINT = {
    "工具型": "读者把它当资料存，不是当故事看。标题要能被搜到（酒店名 + 房型/攻略/避雷），"
    "正文必须给可核对的具体信息（房号、面积、班车时间、加价规则），否则存了也用不上。",
    "争议型": "评论多过点赞，说明观点有对立面。写法上留一个能被反驳的判断，"
    "评论区会自己吵起来，流量跟着来；但结尾要收得住，别把火引到代订业务上。",
    "情绪型": "点赞高、收藏低，是情绪推动的。适合避雷和踩坑，传播快但沉淀弱，"
    "结尾必须给一个明确动作，不然看完就划走。",
    "平衡型": "赞藏评比例均衡，属于标准爆款结构。可以整套拆骨架，风险最低。",
}


def derive_signals(doc: Dict[str, object]) -> List[Ann]:
    """按赞藏评比例判断这篇是哪种类型，并给出对应写法建议。"""
    try:
        metrics = json.loads(str(doc.get("metrics_json") or "{}"))
    except Exception:
        return []
    likes = int(metrics.get("likes") or 0)
    saves = int(metrics.get("saves") or 0)
    comments = int(metrics.get("comments") or 0)
    if likes < 20:
        return []

    save_ratio = saves / likes
    comment_ratio = comments / likes

    if comment_ratio >= 0.45:
        kind = "争议型"
    elif save_ratio >= 1.3:
        kind = "工具型"
    elif save_ratio <= 0.35 and likes >= 200:
        kind = "情绪型"
    else:
        kind = "平衡型"

    out = [
        Ann(
            kind="signal",
            label=f"{kind}笔记",
            text=ACTION_HINT[kind],
            evidence=f"赞 {likes} / 藏 {saves} / 评 {comments}"
            f"（藏赞比 {save_ratio:.2f}，评赞比 {comment_ratio:.2f}）",
            origin="derived",
            weight=0.9,
        )
    ]
    # 具体的比例异常再单独点一句，比笼统结论有用
    if save_ratio >= 2.0:
        out.append(
            Ann(
                kind="signal",
                label="收藏是点赞的两倍以上",
                text="这种笔记在搜索结果里活得久，靠的是长尾搜索而不是推荐流。"
                "标题里的酒店名和品类词不能省，正文摘要要能直接被摘走。",
                evidence=f"藏 {saves} / 赞 {likes}",
                origin="derived",
                weight=0.85,
            )
        )
    if comments >= 100 and likes and comments / likes >= 0.8:
        out.append(
            Ann(
                kind="signal",
                label="评论接近或超过点赞",
                text="典型的众包贴：读者在评论区互相给答案。写这类不要下死结论，"
                "把问题抛准，让评论区替你补细节。",
                evidence=f"评 {comments} / 赞 {likes}",
                origin="derived",
                weight=0.85,
            )
        )
    return out


TITLE_PATTERNS = (
    (("避雷", "劝退", "别住", "踩坑", "翻车", "别盲目"), "风险前置", "把负面判断放在标题最前面，"
     "刷到的人立刻代入「我会不会也踩」，点开是自保不是好奇。避雷帖的评论转化最高。"),
    (("vs", "对比", "横评", "选一个", "怎么选", "四选一", "三选一"), "对比框架", "给的是决策框架而不是单点推荐。"
     "读者收藏是为了拿着对照下单，这类最容易被代订当工具转发。"),
    (("最全", "全攻略", "指南", "大全", "一篇讲清", "Vol", "期"), "工具承诺", "标题承诺了完整性，"
     "读者预期是「存下来慢慢用」。承诺要兑现在正文的信息密度上，不然收藏高但不涨粉。"),
    (("实测", "亲测", "住了", "体验", "自费", "我"), "第一人称实测", "强调亲自住过，"
     "在一堆二手攻略里这是最硬的信任状。代价是必须写细节，写虚了就露。"),
    (("带娃", "亲子", "情侣", "闺蜜", "老人", "一家"), "人群直呼", "标题就把人群框死，"
     "不属于这类的人自动划走，属于的会觉得「这是写给我的」。点击率靠的是精准而不是覆盖。"),
    (("元", "块", "晚", "折", "价", "预算", "省"), "价格锚点", "把价格写进标题，"
     "读者的判断基准立刻建立。价格是代订最敏感的信息，但也是最容易过期的，必须标日期。"),
)
NUMBER_RE = re.compile(r"\d")


def derive_title(doc: Dict[str, object]) -> List[Ann]:
    title = str(doc.get("title") or "")
    if not title:
        return []
    out: List[Ann] = []
    low = title.lower()
    for keys, label, why in TITLE_PATTERNS:
        if any(k.lower() in low for k in keys):
            out.append(
                Ann(
                    kind="title",
                    label=f"标题手法：{label}",
                    text=why,
                    evidence=f"标题：{title}",
                    origin="derived",
                    weight=0.88,
                )
            )
    if NUMBER_RE.search(title):
        out.append(
            Ann(
                kind="title",
                label="标题手法：数字具体化",
                text="标题里有数字，读者能立刻估出信息量（几家、几晚、多少钱、第几期）。"
                "数字要真实可核对，编一个整数没有说服力。",
                evidence=f"标题：{title}",
                origin="derived",
                weight=0.8,
            )
        )
    return out[:3]


# ---------------------------------------------------------------- 落库


def _insert(conn, doc_id: int, anns: List[Ann]) -> int:
    if not anns:
        return 0
    conn.executemany(
        "INSERT INTO annotations(doc_id, kind, label, text, evidence, origin, weight, seq)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [
            (doc_id, a.kind, a.label, a.text, a.evidence, a.origin, round(a.weight, 3), a.seq)
            for a in anns
        ],
    )
    return len(anns)


def run(conn, logger=None) -> Dict[str, int]:
    """全量重算注解。

    纯正则、不读盘（原文已经在 docs.content 里），全库也就一两秒，
    所以不做增量失效判断 —— 少一套状态，就少一处对不上。
    """
    conn.execute("DELETE FROM annotations")
    daily = [
        r["content"]
        for r in conn.execute("SELECT content FROM docs WHERE doc_type='daily'").fetchall()
    ]
    review_index = build_review_index(daily)

    kind_counts: Dict[str, int] = {}
    reviews = 0
    total = 0
    rows = conn.execute(
        "SELECT id, title, rel_path, doc_type, grade, content, metrics_json FROM docs"
    ).fetchall()
    for row in rows:
        anns = annotations_from_sections(row["content"] or "")
        if row["doc_type"] in ("note", "guide", "fact"):
            stem = (row["rel_path"] or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            hit = match_review([row["title"] or "", stem], review_index)
            if hit:
                reviews += 1
                anns.append(
                    Ann(
                        kind="review",
                        label="采集点评",
                        text=hit["review"],
                        evidence=f"{hit['grade']} 级 · {hit['metrics']}" if hit["metrics"] else f"{hit['grade']} 级",
                        origin="source",
                        weight=1.25,
                    )
                )
            anns.extend(derive_signals(dict(row)))
            anns.extend(derive_title(dict(row)))
        anns.sort(key=lambda a: -a.weight)
        for i, ann in enumerate(anns):
            ann.seq = i
        total += _insert(conn, row["id"], anns)
        for ann in anns:
            kind_counts[ann.kind] = kind_counts.get(ann.kind, 0) + 1

    conn.commit()
    if logger:
        logger(f"注解 {total} 条，其中采集点评匹配 {reviews} 篇")
    return {"total": total, "reviews": reviews, "by_kind": kind_counts}


def for_doc(conn, doc_id: int, limit: int = 40) -> List[Dict[str, object]]:
    """取一篇的注解，按权重降序 —— 打开笔记第一眼看到的就是最该看的那条。"""
    rows = conn.execute(
        "SELECT kind, label, text, evidence, origin, weight FROM annotations "
        "WHERE doc_id=? ORDER BY weight DESC, seq LIMIT ?",
        (doc_id, limit),
    ).fetchall()
    return [
        {
            "kind": r["kind"],
            "kind_label": ANN_KINDS.get(r["kind"], r["kind"]),
            "label": r["label"],
            "text": r["text"],
            "evidence": r["evidence"] or "",
            "origin": r["origin"] or "source",
            "weight": round(float(r["weight"] or 0), 2),
        }
        for r in rows
    ]


def counts(conn) -> Dict[str, int]:
    return {
        "annotations": conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0],
        "annotated_docs": conn.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM annotations"
        ).fetchone()[0],
    }
