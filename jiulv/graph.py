"""概念图：把笔记拆成「概念 ↔ 笔记」的二部结构，再按需拼成局部图。

Obsidian 的图谱之所以有用，不是因为线多，而是因为节点是同一种东西。这里也一样：
地区、酒店、作者、人群、痛点、钩子、手法全部是一等节点，笔记也是节点，
两者之间只靠 ``doc_concepts`` 一条边表连接，正着查是「这篇讲了什么」，
反着查就是「谁还讲过这个」—— 也就是反向链接。

不预存笔记两两之间的边。同地区几百篇两两相连会炸成 O(n²)，
真要看邻居时现算一跳就够了，语义反而更准。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .annotate import TITLE_PATTERNS
from .taxonomy import load_taxonomy

CLAUSE_SPLIT = re.compile(r"[。！？；，,.!?;：:、\s]+")

CONCEPT_KINDS = {
    "region": "地区",
    "hotel": "酒店",
    "author": "作者",
    "series": "系列",
    "audience": "人群",
    "pain": "痛点",
    "hook": "钩子",
    "technique": "手法",
}

# 小红书默认昵称，不是真人，别建成作者节点
GENERIC_AUTHORS = {"momo", "小红薯", "已注销", "用户", "薯队长", "薯管家"}
GENERIC_AUTHOR_RE = re.compile(r"^(momo|小红薯[0-9a-zA-Z]*|用户[0-9a-zA-Z]*)$", re.I)

HOTEL_NOISE_RE = re.compile(
    r"(度假酒店|度假村|大酒店|酒店|饭店|宾馆|Hotel|Resort|Villas?|Spa|Express)", re.I
)
VOL_RE = re.compile(r"[（(]?\s*(?:Vol\.?|VOL\.?|第)\s*(\d{1,3})\s*(?:期)?\s*[）)]?", re.I)


def norm_hotel(name: str) -> str:
    """把「三亚保利瑰丽酒店（international，瑰丽集团）」归成「三亚保利瑰丽」。

    资料里同一家酒店常带集团注释、括号备注，甚至一张帖子里列好几家。
    不归并的话图谱里就是一堆孤点，看着热闹、点开全是空的。
    """
    s = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    s = re.split(r"[；;、]", s)[0]
    s = HOTEL_NOISE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ·-—")
    return s


def norm_author(name: str) -> Optional[str]:
    s = (name or "").strip()
    if len(s) < 2 or s in GENERIC_AUTHORS or GENERIC_AUTHOR_RE.match(s):
        return None
    return s


def series_of(title: str) -> Optional[str]:
    """「三亚文华东方最全选房指南Vol.08」→「三亚文华东方最全选房指南」。"""
    t = title or ""
    if not VOL_RE.search(t):
        return None
    base = VOL_RE.sub("", t).strip(" ·-—")
    return base if len(base) >= 6 else None


def technique_of(title: str) -> List[str]:
    low = (title or "").lower()
    out: List[str] = []
    for keys, label, _why in TITLE_PATTERNS:
        if any(k.lower() in low for k in keys):
            out.append(label)
    if re.search(r"\d", title or ""):
        out.append("数字具体")
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- 构建


def _upsert_concept(conn, kind: str, key: str, label: str, note: str = "") -> int:
    conn.execute(
        "INSERT INTO concepts(kind, key, label, note) VALUES(?,?,?,?) "
        "ON CONFLICT(kind, key) DO UPDATE SET label=excluded.label, "
        "note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE concepts.note END",
        (kind, key, label, note),
    )
    row = conn.execute(
        "SELECT id FROM concepts WHERE kind=? AND key=?", (kind, key)
    ).fetchone()
    return int(row["id"])


def _link(conn, doc_id: int, concept_id: int, weight: float, evidence: str = "") -> None:
    conn.execute(
        "INSERT INTO doc_concepts(doc_id, concept_id, weight, evidence) VALUES(?,?,?,?) "
        "ON CONFLICT(doc_id, concept_id) DO UPDATE SET "
        "weight=MAX(doc_concepts.weight, excluded.weight)",
        (doc_id, concept_id, round(float(weight), 3), evidence),
    )


def _hit_counts(text: str, aliases: Iterable[str]) -> int:
    return sum(text.count(a) for a in aliases if a)


# 能进图的文档类型。misc / ops / index / daily 是流程和索引文件，
# 连上去只会让「三亚保利瑰丽」下面挂一堆 raw_data，看不出任何规律。
GRAPH_DOC_TYPES = {"note", "guide", "fact", "hook", "rule"}
# 导出快照和索引页不是笔记，标题里带这些词的一律不进图
DUMP_TITLE_RE = re.compile(
    r"(raw_data|全量素材原文|快照版|导出版|\.json|\.csv|\.xlsx|索引|生成器|学习包|README|总览|清单)",
    re.I,
)


def dedup_key(title: str, doc_type: str, uid: str) -> Tuple[str, str]:
    """折叠副本用的键。标题里带括号说明和 emoji 的算同一篇
    （「（有后续版）」和「（🈶后续版）」是同一篇的两次采集）。"""
    t = re.sub(r"[（(][^）)]*[）)]", "", title or "")
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"[\s\-_、，,。.！!？?「」“”\"'·/|:：;；]+", "", t)
    if len(t) < 5:
        return ("uid", uid)
    return (t, doc_type or "")


def main_doc_ids(conn) -> set:
    """同一篇笔记常有多份副本（不同快照、不同来源各存一份）。

    图里只留一份，否则「三亚保利瑰丽」下面会挂四个同名节点，
    看着像四篇不同笔记，点开发现是同一篇 —— 这比没有图谱更糟。
    去重键用「标题+类型」，标题太短的（「避雷！！！」）不敢折叠，用 uid 单独算。
    """
    rows = conn.execute(
        "SELECT id, uid, title, doc_type FROM docs "
        "ORDER BY CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,"
        " char_count DESC, id"
    ).fetchall()
    seen: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = dedup_key(row["title"] or "", row["doc_type"] or "", row["uid"])
        seen.setdefault(key, int(row["id"]))
    return set(seen.values())


def build(conn, config=None, logger=None) -> Dict[str, int]:
    """重建全部概念与连接。同样全量重算，理由和注解那边一致。"""
    conn.execute("DELETE FROM doc_concepts")
    conn.execute("DELETE FROM concepts")

    taxonomy = load_taxonomy(config.get("taxonomy") if config else None)
    audiences = {
        code: (meta.get("label", code), list(meta.get("aliases") or []))
        for code, meta in (taxonomy.get("audience") or {}).items()
    }
    pains = {
        code: (meta.get("label", code), list(meta.get("aliases") or []))
        for code, meta in (taxonomy.get("pain") or {}).items()
    }

    # 先统计作者出现次数，够格的才建节点
    author_counts = {
        row["author"]: row["n"]
        for row in conn.execute(
            "SELECT author, COUNT(*) n FROM docs WHERE author<>'' GROUP BY author"
        )
    }

    # 钩子：拿原句的子句去正文里碰。整句碰不上 —— 钩子是作者自己写的，
    # 别人复述时总会改几个字，子句级别才可能撞上。
    hook_clauses: List[Tuple[str, List[str]]] = []
    for row in conn.execute("SELECT code, text FROM hooks WHERE text<>''"):
        flat = re.sub(r"\s+", "", row["text"] or "")
        clauses = [c for c in CLAUSE_SPLIT.split(flat) if len(c) >= 8]
        if clauses:
            hook_clauses.append((row["code"], clauses))

    counts: Dict[str, int] = {}
    linked = 0
    mains = main_doc_ids(conn)
    skipped = 0

    # 酒店名先从全库收一遍：很多笔记的 hotel 字段是空的，但标题里明明白白写着，
    # 不兜这一层，「三亚保利瑰丽卫生堪忧」就挂不到「三亚保利瑰丽」上去。
    hotel_names = sorted(
        {
            norm_hotel(r[0] or "")
            for r in conn.execute("SELECT DISTINCT hotel FROM docs WHERE hotel<>''")
        }
        - {""},
        key=len,
        reverse=True,
    )
    hotel_names = [h for h in hotel_names if len(h) >= 3 and not re.match(r"^\d", h)]

    rows = conn.execute(
        "SELECT id, title, doc_type, grade, region, hotel, author, content, metrics_json FROM docs"
    ).fetchall()

    for row in rows:
        doc_id = int(row["id"])
        if doc_id not in mains:
            skipped += 1
            continue
        if (row["doc_type"] or "") not in GRAPH_DOC_TYPES or DUMP_TITLE_RE.search(row["title"] or ""):
            skipped += 1
            continue
        content = row["content"] or ""
        loose = re.sub(r"\s+", "", content)
        touched: List[Tuple[str, str, str, float, str]] = []

        region = (row["region"] or "").strip()
        if region:
            touched.append(("region", region, region, 1.0, ""))

        hotel = norm_hotel(row["hotel"] or "")
        if hotel and len(hotel) >= 2 and not re.match(r"^\d", hotel):
            touched.append(("hotel", hotel, hotel, 1.2, row["hotel"] or ""))
        elif not hotel:
            flat_title = re.sub(r"\s+", "", row["title"] or "")
            for name in hotel_names:
                if name in flat_title:
                    touched.append(("hotel", name, name, 0.9, "标题含此酒店名"))
                    break

        author = norm_author(row["author"] or "")
        if author and author_counts.get(row["author"], 0) >= 3:
            touched.append(("author", author, author, 0.5, ""))

        series = series_of(row["title"] or "")
        if series:
            touched.append(("series", series, series, 0.9, row["title"] or ""))

        for code, label, why in TITLE_PATTERNS:
            if any(k.lower() in (row["title"] or "").lower() for k in code):
                touched.append(("technique", label, label, 0.8, why))

        # 人群与痛点：按别名在正文的命中次数排序，每类最多两个，避免一张图全是标签
        for kind, table in (("audience", audiences), ("pain", pains)):
            scored = []
            for code, (label, aliases) in table.items():
                n = _hit_counts(content, aliases)
                if n >= 3:
                    scored.append((n, code, label))
            scored.sort(reverse=True)
            for n, code, label in scored[:2]:
                touched.append((kind, code, f"{code} {label}", min(1.5, 0.5 + n / 40), f"命中 {n} 次"))

        # 钩子：子句命中才连，宁可漏也不要硬连
        for code, clauses in hook_clauses:
            if any(c in loose for c in clauses):
                touched.append(("hook", code, code, 1.0, "正文含该钩子原句片段"))

        for kind, key, label, weight, evidence in touched:
            note = ""
            if kind == "audience" and key in audiences:
                note = audiences[key][0]
            elif kind == "pain" and key in pains:
                note = pains[key][0]
            cid = _upsert_concept(conn, kind, key, label, note)
            _link(conn, doc_id, cid, weight, evidence)
            linked += 1
            counts[kind] = counts.get(kind, 0) + 1

    conn.execute(
        "UPDATE concepts SET doc_count=(SELECT COUNT(*) FROM doc_concepts dc "
        "WHERE dc.concept_id=concepts.id)"
    )
    conn.commit()
    if logger:
        kinds = "，".join(f"{CONCEPT_KINDS.get(k, k)} {v}" for k, v in sorted(counts.items()))
        logger(f"概念 {len(counts)} 类，连接 {linked} 条（{kinds}），折叠副本 {skipped} 篇")
    return counts


# ---------------------------------------------------------------- 查询


def _node_concept(row) -> Dict[str, Any]:
    return {
        "id": f"c:{row['id']}",
        "label": row["label"],
        "kind": row["kind"],
        "kind_label": CONCEPT_KINDS.get(row["kind"], row["kind"]),
        "key": row["key"],
        "ref": f"{row['kind']}:{row['key']}",
        "size": max(1, int(row["doc_count"] or 1)),
        "note": row["note"] or "",
    }


def _node_doc(row) -> Dict[str, Any]:
    return {
        "id": f"d:{row['id']}",
        "label": row["title"] or "(无标题)",
        "kind": "doc",
        "kind_label": f"{row['grade'] or 'C'} 级",
        "size": 3,
        "grade": row["grade"] or "",
        "doc_type": row["doc_type"] or "",
        "uid": row["uid"],
        "region": row["region"] or "",
        "hotel": row["hotel"] or "",
        "metrics": _metrics(row),
    }


def _metrics(row) -> Dict[str, int]:
    import json

    try:
        return json.loads(row["metrics_json"] or "{}")
    except Exception:
        return {}


def _concept_by_ref(conn, ref: str):
    """ref 支持 c:12 / hotel:三亚保利瑰丽 / 裸标签。"""
    if ref.startswith("c:"):
        return conn.execute("SELECT * FROM concepts WHERE id=?", (ref[2:],)).fetchone()
    if ":" in ref:
        kind, key = ref.split(":", 1)
        row = conn.execute(
            "SELECT * FROM concepts WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM concepts WHERE label=? ORDER BY doc_count DESC LIMIT 1", (ref,)
    ).fetchone()


def overview(conn, limit: int = 55, min_share: int = 3) -> Dict[str, Any]:
    """无焦点时的全景图：节点是概念，边是两个概念共同出现过的笔记数。

    这才是「知识连在一起」的样子 —— 不是一堆笔记挤在一起，
    而是「海南」和「带娃」「房型不符」之间本来就有的关系。
    """
    rows = conn.execute(
        "SELECT * FROM concepts WHERE doc_count>=? ORDER BY doc_count DESC LIMIT ?",
        (min_share, limit * 2),
    ).fetchall()

    # 人群和痛点几乎跟每篇都连着，全画出来中间就是一坨。
    # 每类只留度数最高的几个，想看全的进单项概念页。
    kept: List[Any] = []
    per_kind: Dict[str, int] = {}
    for row in rows:
        cap = 3 if row["kind"] in ("audience", "pain") else 99
        if per_kind.get(row["kind"], 0) >= cap:
            continue
        per_kind[row["kind"]] = per_kind.get(row["kind"], 0) + 1
        kept.append(row)
        if len(kept) >= limit:
            break

    allowed = {int(r["id"]): r for r in kept}
    if not allowed:
        return {"nodes": [], "edges": [], "focus": None}

    placeholders = ",".join("?" for _ in allowed)
    edges: Dict[Tuple[int, int], int] = {}
    for row in conn.execute(
        f"SELECT a.concept_id x, b.concept_id y, COUNT(*) n FROM doc_concepts a "
        f"JOIN doc_concepts b ON a.doc_id=b.doc_id AND a.concept_id<b.concept_id "
        f"WHERE a.concept_id IN ({placeholders}) AND b.concept_id IN ({placeholders}) "
        f"GROUP BY 1,2 HAVING n>=2",
        list(allowed) + list(allowed),
    ):
        edges[(int(row["x"]), int(row["y"]))] = int(row["n"])

    # 一条共现都没有的节点是纯噪点，点开也是空的，不如不画
    linked = {node for pair in edges for node in pair}
    nodes = [_node_concept(r) for cid, r in allowed.items() if cid in linked] or [
        _node_concept(r) for r in kept[:10]
    ]
    live = {int(n["id"][2:]) for n in nodes}
    return {
        "focus": None,
        "nodes": nodes,
        "edges": [
            {
                "source": f"c:{x}",
                "target": f"c:{y}",
                "relation": f"{n} 篇共现",
                "weight": n,
            }
            for (x, y), n in edges.items()
            if x in live and y in live
        ],
    }


def subgraph(conn, ref: str, limit: int = 24) -> Dict[str, Any]:
    """以某个概念或某篇笔记为中心的一跳子图。"""
    if ref.startswith("d:"):
        return _doc_subgraph(conn, int(ref[2:]), limit)

    concept = _concept_by_ref(conn, ref)
    if concept is None:
        return {"focus": None, "nodes": [], "edges": []}
    cid = int(concept["id"])

    docs = conn.execute(
        "SELECT d.*, dc.weight w, dc.evidence ev FROM doc_concepts dc JOIN docs d ON d.id=dc.doc_id "
        "WHERE dc.concept_id=? ORDER BY CASE d.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
        "WHEN 'B' THEN 2 ELSE 3 END, dc.weight DESC, d.id DESC LIMIT ?",
        (cid, limit),
    ).fetchall()

    nodes = [_node_concept(concept)] + [_node_doc(r) for r in docs]
    edges = [
        {
            "source": f"c:{cid}",
            "target": f"d:{int(r['id'])}",
            "relation": r["ev"] or "提及",
            "weight": float(r["w"] or 1),
        }
        for r in docs
    ]

    # 这些笔记共同指向的其他概念，用来露出「同一个东西还被谁串着」
    doc_ids = [int(r["id"]) for r in docs]
    if doc_ids:
        ph = ",".join("?" for _ in doc_ids)
        media = conn.execute(
            f"SELECT c.*, COUNT(*) n FROM doc_concepts dc JOIN concepts c ON c.id=dc.concept_id "
            f"WHERE dc.doc_id IN ({ph}) AND dc.concept_id<>? "
            f"GROUP BY c.id ORDER BY n DESC, c.doc_count DESC LIMIT 8",
            doc_ids + [cid],
        ).fetchall()
        for row in media:
            nodes.append(_node_concept(row))
            for d in doc_ids:
                hit = conn.execute(
                    "SELECT 1 FROM doc_concepts WHERE doc_id=? AND concept_id=?",
                    (d, row["id"]),
                ).fetchone()
                if hit:
                    edges.append(
                        {
                            "source": f"c:{row['id']}",
                            "target": f"d:{d}",
                            "relation": "同篇共现",
                            "weight": 0.4,
                        }
                    )

    return {
        "focus": _node_concept(concept),
        "nodes": nodes,
        "edges": edges,
    }


def _doc_subgraph(conn, doc_id: int, limit: int = 12) -> Dict[str, Any]:
    doc = conn.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    if doc is None:
        return {"focus": None, "nodes": [], "edges": []}

    concepts = conn.execute(
        "SELECT c.*, dc.weight w, dc.evidence ev FROM doc_concepts dc JOIN concepts c ON c.id=dc.concept_id "
        "WHERE dc.doc_id=? ORDER BY c.doc_count DESC LIMIT 12",
        (doc_id,),
    ).fetchall()

    nodes = [_node_doc(doc)] + [_node_concept(r) for r in concepts]
    edges = [
        {
            "source": f"d:{doc_id}",
            "target": f"c:{int(r['id'])}",
            "relation": r["ev"] or "提及",
            "weight": float(r["w"] or 1),
        }
        for r in concepts
    ]

    # 每个概念取几篇兄弟，构成「反向链接」的可视化
    for row in concepts:
        siblings = conn.execute(
            "SELECT d.* FROM doc_concepts dc JOIN docs d ON d.id=dc.doc_id "
            "WHERE dc.concept_id=? AND dc.doc_id<>? "
            "ORDER BY CASE d.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, d.id DESC "
            "LIMIT ?",
            (int(row["id"]), doc_id, max(2, limit // max(len(concepts), 1))),
        ).fetchall()
        for s in siblings:
            sid = f"d:{int(s['id'])}"
            if any(n["id"] == sid for n in nodes):
                continue
            nodes.append(_node_doc(s))
            edges.append(
                {
                    "source": f"c:{int(row['id'])}",
                    "target": sid,
                    "relation": f"同{CONCEPT_KINDS.get(row['kind'], '类')}",
                    "weight": 0.5,
                }
            )

    return {"focus": _node_doc(doc), "nodes": nodes, "edges": edges}


def concept_docs(conn, ref: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    concept = _concept_by_ref(conn, ref)
    if concept is None:
        return {"concept": None, "items": [], "total": 0}
    cid = int(concept["id"])
    total = conn.execute(
        "SELECT COUNT(*) FROM doc_concepts WHERE concept_id=?", (cid,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT d.*, dc.evidence ev FROM doc_concepts dc JOIN docs d ON d.id=dc.doc_id "
        "WHERE dc.concept_id=? ORDER BY CASE d.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
        "WHEN 'B' THEN 2 ELSE 3 END, d.id DESC LIMIT ? OFFSET ?",
        (cid, limit, offset),
    ).fetchall()
    items = []
    for r in rows:
        item = {
            "uid": r["uid"],
            "title": r["title"],
            "doc_type": r["doc_type"],
            "grade": r["grade"],
            "region": r["region"],
            "hotel": r["hotel"],
            "author": r["author"],
            "metrics": _metrics(r),
            "evidence": r["ev"] or "",
        }
        items.append(item)
    return {
        "concept": _node_concept(concept),
        "total": total,
        "items": items,
    }


def backlinks(conn, doc_id: int, limit: int = 6) -> List[Dict[str, Any]]:
    """反向链接：通过共享概念找上来的其他笔记，按共享概念数排序。"""
    rows = conn.execute(
        """
        SELECT d.uid, d.title, d.grade, d.region, d.hotel, d.doc_type, d.metrics_json,
               COUNT(*) shared, GROUP_CONCAT(c.label, '、') via
        FROM doc_concepts a
        JOIN doc_concepts b ON a.concept_id=b.concept_id AND a.doc_id<>b.doc_id
        JOIN concepts c ON c.id=a.concept_id
        JOIN docs d ON d.id=a.doc_id
        WHERE b.doc_id=?
        GROUP BY a.doc_id
        ORDER BY shared DESC,
                 CASE d.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
                 a.doc_id DESC
        LIMIT ?
        """,
        (doc_id, limit),
    ).fetchall()
    return [
        {
            "uid": r["uid"],
            "title": r["title"],
            "grade": r["grade"],
            "region": r["region"],
            "hotel": r["hotel"],
            "doc_type": r["doc_type"],
            "metrics": _metrics(r),
            "shared": int(r["shared"]),
            "via": (r["via"] or "").split("、")[:3],
        }
        for r in rows
    ]


def doc_concepts(conn, doc_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.*, dc.weight w, dc.evidence ev FROM doc_concepts dc JOIN concepts c ON c.id=dc.concept_id "
        "WHERE dc.doc_id=? ORDER BY dc.weight DESC, c.doc_count DESC",
        (doc_id,),
    ).fetchall()
    out = []
    for r in rows:
        node = _node_concept(r)
        node["weight"] = float(r["w"] or 1)
        node["evidence"] = r["ev"] or ""
        out.append(node)
    return out


def by_kind(conn, kind: str, limit: int = 40) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM concepts WHERE kind=? ORDER BY doc_count DESC, label LIMIT ?",
        (kind, limit),
    ).fetchall()
    return [_node_concept(r) for r in rows]
