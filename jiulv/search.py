"""检索：关键词（BM25）、语义（向量）、混合（RRF 融合）。

关键词走 FTS5 + 自建中文二元分词，一行 SQL 就出结果；
语义是可选的第二路，两路用 RRF 合并，避免任何一路的分数尺度压死另一路。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import embeddings as emb
from .config import Config
from .db import doc_row_to_dict
from .tokenizer import build_match_expr, query_tokens

FILTER_COLUMNS = {
    "region": "d.region",
    "doc_type": "d.doc_type",
    "hotel": "d.hotel",
    "group": "d.group_name",
    "grade": "d.grade",
    "source": "d.source",
}

MARK_OPEN, MARK_CLOSE = "«", "»"

# 不同类型的文件对「写稿」的价值不一样：索引用不上，爆款和规则最有用
TYPE_WEIGHT = {"index": 0.4, "daily": 0.7, "misc": 0.85, "ops": 0.85}
GRADE_BONUS = {"S": 1.25, "A": 1.12}
FIELD_LINE_RE = re.compile(
    r"^[\-\*>\s|]*\*{0,2}(标题|作者|发布时间|互动|地区|酒店|brand|品牌|采集日期|链接|编号|来源)\*{0,2}[：:]",
    re.I,
)


def clean_snippet_text(text: str) -> str:
    """把片段整理成能读的一段：去掉字段行、列表符号和 Markdown 强调符号。"""
    lines: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or FIELD_LINE_RE.match(line):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = line.replace("**", "").replace("__", "")
        lines.append(line)
    return " · ".join(lines)


def make_snippet(text: str, query: str, limit: int = 220) -> str:
    """从原文截一段带命中标记的片段。

    不用 FTS5 的 snippet()：索引列里存的是二元分词串，取出来会是「三亚» 亚仁 仁恒」
    这种鬼东西。这里回到原文找位置，只标用户真正输入的词。
    """
    body = re.sub(r"\s+", " ", clean_snippet_text(text)).strip()
    if not body:
        return ""
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if t]
    lower = body.lower()
    pos = -1
    for term in terms:
        found = lower.find(term.lower())
        if found != -1 and (pos == -1 or found < pos):
            pos = found
    if pos == -1:
        return body[:limit] + ("…" if len(body) > limit else "")
    start = max(0, pos - limit // 3)
    end = min(len(body), start + limit)
    frag = body[start:end]
    if terms:
        pattern = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.I)
        frag = pattern.sub(lambda m: f"{MARK_OPEN}{m.group(0)}{MARK_CLOSE}", frag)
    return ("…" if start > 0 else "") + frag + ("…" if end < len(body) else "")


def _build_filters(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for key, column in FILTER_COLUMNS.items():
        value = filters.get(key)
        if value in (None, "", []):
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"{column} IN ({placeholders})")
        params.extend(values)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _chunk_hits(
    conn, tokens: Sequence[str], filters: Dict[str, Any], limit: int, operator: str = "AND"
) -> List[Dict[str, Any]]:
    expr = build_match_expr(tokens, operator)
    if not expr:
        return []
    where, params = _build_filters(filters)
    sql = f"""
        SELECT chunks_fts.chunk_id AS chunk_id,
               chunks_fts.doc_id   AS doc_id,
               c.heading           AS heading,
               c.seq               AS seq,
               c.text              AS body,
               bm25(chunks_fts, 0.0, 0.0, 4.0, 1.0) AS rank
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.chunk_id
        JOIN docs d   ON d.id = chunks_fts.doc_id
        WHERE chunks_fts MATCH ? {where}
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, [expr] + params + [limit]).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _title_hits(conn, tokens: Sequence[str], filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    expr = build_match_expr(tokens, "AND")
    if not expr:
        return []
    where, params = _build_filters(filters)
    sql = f"""
        SELECT docs_fts.doc_id AS doc_id, bm25(docs_fts, 0.0, 1.0) AS rank
        FROM docs_fts JOIN docs d ON d.id = docs_fts.doc_id
        WHERE docs_fts MATCH ? {where}
        ORDER BY rank LIMIT ?
    """
    try:
        rows = conn.execute(sql, [expr] + params + [limit]).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _semantic_chunk_hits(conn, config: Config, query: str, filters: Dict[str, Any], limit: int):
    pairs = emb.semantic_search(conn, config, query, top_k=max(limit * 3, 60))
    if not pairs:
        return []
    out: List[Dict[str, Any]] = []
    where, params = _build_filters(filters)
    for chunk_id, score in pairs:
        row = conn.execute(
            f"""
            SELECT c.id AS chunk_id, c.doc_id AS doc_id, c.heading, c.seq, c.text,
                   d.region, d.doc_type, d.group_name, d.grade, d.source, d.hotel
            FROM chunks c JOIN docs d ON d.id = c.doc_id
            WHERE c.id = ? {where}
            """,
            [chunk_id] + params,
        ).fetchone()
        if not row:
            continue
        item = dict(row)
        item["body"] = row["text"]
        item["rank"] = -score  # 统一成「越小越好」，跟 bm25 一致
        item["sim"] = score
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _aggregate(chunk_rows: List[Dict[str, Any]], conn) -> Dict[int, Dict[str, Any]]:
    per_doc: Dict[int, Dict[str, Any]] = {}
    for row in chunk_rows:
        doc_id = row["doc_id"]
        entry = per_doc.setdefault(
            doc_id, {"score": 0.0, "matched": 0, "bodies": [], "headings": [], "sim": 0.0}
        )
        entry["matched"] += 1
        rank = row.get("rank", 0.0)
        entry["score"] = max(entry["score"], -rank)
        if row.get("sim"):
            entry["sim"] = max(entry["sim"], row["sim"])
        body = row.get("body")
        if body and all(item[2] != body for item in entry["bodies"]) and len(entry["bodies"]) < 4:
            entry["bodies"].append((float(-rank), row.get("chunk_id"), body))
        if row.get("heading") and row["heading"] not in entry["headings"]:
            entry["headings"].append(row["heading"])
    return per_doc


def _pick_body(entry: Dict[str, Any]) -> str:
    """优先取信息量够的那段：字段行、目录行别拿来当摘要。"""
    bodies = sorted(entry.get("bodies") or [], key=lambda item: item[0])

    def informative(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        field_lines = sum(1 for line in lines if FIELD_LINE_RE.match(line))
        return field_lines < max(1, len(lines) * 0.6)

    for _, _, body in bodies:
        if len(body) >= 140 and informative(body):
            return body
    for _, _, body in bodies:
        if informative(body):
            return body
    return bodies[0][2] if bodies else ""


def _load_docs(conn, doc_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    if not doc_ids:
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"SELECT * FROM docs WHERE id IN ({placeholders})", list(doc_ids)
    ).fetchall()
    return {row["id"]: doc_row_to_dict(row) for row in rows}


def keyword_candidates(conn, query: str, filters: Dict[str, Any], limit: int = 120):
    tokens = query_tokens(query)
    if not tokens:
        return []
    rows = _chunk_hits(conn, tokens, filters, limit, "AND")
    used = "AND"
    if not rows:
        rows = _chunk_hits(conn, tokens, filters, limit, "OR")
        used = "OR"
    return rows, tokens, used


def search(
    conn,
    query: str,
    config: Optional[Config] = None,
    limit: int = 10,
    offset: int = 0,
    mode: str = "auto",
    **filters: Any,
) -> Dict[str, Any]:
    """统一检索入口。mode: auto | keyword | semantic | hybrid。"""
    limit = max(1, min(int(limit), 50))
    filters = {k: v for k, v in filters.items() if v not in (None, "", [])}
    semantic_ready = bool(
        config and config.get("semantic", "enabled", default=False)
        and conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] > 0
    )
    if mode == "auto":
        mode = "hybrid" if semantic_ready else "keyword"

    cand: List[Dict[str, Any]] = []
    tokens: List[str] = []
    operator = "AND"
    if mode in ("keyword", "hybrid"):
        rows, tokens, operator = keyword_candidates(conn, query, filters, limit=180)
        cand.extend(rows)
        title_rows = _title_hits(conn, tokens, filters, 40)
        for row in title_rows:
            cand.append(
                {
                    "doc_id": row["doc_id"],
                    "chunk_id": -1,
                    "heading": "",
                    "body": "",
                    "rank": row["rank"] - 2.0,  # 命中标题额外加权
                }
            )
    if mode in ("semantic", "hybrid") and semantic_ready and config is not None:
        cand.extend(_semantic_chunk_hits(conn, config, query, filters, limit=120))

    per_doc = _aggregate(cand, conn)

    # 混合模式下用 RRF 重新打分，避免 BM25 与余弦尺度打架
    if mode == "hybrid":
        kw_rank = {d: i for i, d in enumerate(
            [d for d, _ in sorted(per_doc.items(), key=lambda kv: -kv[1]["score"])]
        )}
        sem_rank = {d: i for i, d in enumerate(
            [d for d, e in sorted(per_doc.items(), key=lambda kv: -kv[1]["sim"]) if e["sim"] > 0]
        )}
        weight = float(config.get("semantic", "weight", default=0.45)) if config else 0.45
        for doc_id, entry in per_doc.items():
            score = 0.0
            if doc_id in kw_rank:
                score += (1 - weight) / (60 + kw_rank[doc_id])
            if doc_id in sem_rank:
                score += weight / (60 + sem_rank[doc_id])
            entry["score"] = score * 1000

    # 索引类文件动辄列几百个标题，BM25 很容易被它带偏；S/A 级爆款则抬高一点。
    all_docs = _load_docs(conn, list(per_doc.keys()))
    for doc_id, entry in per_doc.items():
        doc = all_docs.get(doc_id) or {}
        entry["score"] *= TYPE_WEIGHT.get(doc.get("doc_type") or "", 1.0)
        entry["score"] *= GRADE_BONUS.get((doc.get("grade") or "").upper(), 1.0)
        # 十几万字的全量快照是语料不是单篇，别跟单篇笔记抢前排
        if (doc.get("char_count") or 0) > 150_000:
            entry["score"] *= 0.45

    ranked = sorted(per_doc.items(), key=lambda kv: -kv[1]["score"])

    # 同一篇笔记常在多份快照、多个来源里各存一份。按「标题+类型」折叠，
    # 只保留分数最高的那份，并记下还有几份重复，原始文件一个都不删。
    collapsed: List[Tuple[int, Dict[str, Any]]] = []
    seen_key: Dict[Any, Dict[str, Any]] = {}
    for doc_id, entry in ranked:
        doc = all_docs.get(doc_id) or {}
        title_key = re.sub(r"[\s，,。！!？?、·]+", "", doc.get("title") or "")
        key: Any = (title_key, doc.get("doc_type")) if len(title_key) >= 6 else doc_id
        if key in seen_key:
            seen_key[key]["duplicates"] += 1
            continue
        entry["duplicates"] = 0
        seen_key[key] = entry
        collapsed.append((doc_id, entry))

    total = len(collapsed)
    window = collapsed[offset : offset + limit]
    snippet_chars = int(config.get("search", "snippet_chars", default=220)) if config else 220

    hits: List[Dict[str, Any]] = []
    for doc_id, entry in window:
        doc = all_docs.get(doc_id)
        if not doc:
            continue
        snippet = make_snippet(_pick_body(entry) or doc.get("summary") or "", query, snippet_chars)
        hits.append(
            {
                "doc": doc,
                "score": round(float(entry["score"]), 4),
                "matched_chunks": entry["matched"],
                "snippet": snippet,
                "headings": entry["headings"][:3],
                "similarity": round(entry["sim"], 4) if entry["sim"] else None,
                "duplicates": entry["duplicates"],
            }
        )
    return {
        "query": query,
        "mode": mode,
        "operator": operator,
        "tokens": tokens,
        "total": total,
        "offset": offset,
        "limit": limit,
        "hits": hits,
    }


# ---------------------------------------------------------------- 结构化的取用


def list_hooks(
    conn,
    hook_type: Optional[str] = None,
    audience: Optional[str] = None,
    pain: Optional[str] = None,
    bucket: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if hook_type:
        clauses.append("hook_type LIKE ?")
        params.append(f"%{hook_type}%")
    if audience:
        tokens = [t for t in str(audience).replace("+", " ").split() if t]
        sub = " OR ".join("audience LIKE ?" for _ in tokens) or "1=1"
        clauses.append(f"({sub})")
        params.extend(f"%{t}%" for t in tokens)
    if pain:
        tokens = [t for t in str(pain).replace("+", " ").split() if t]
        sub = " OR ".join("pain LIKE ?" for _ in tokens) or "1=1"
        clauses.append(f"({sub})")
        params.extend(f"%{t}%" for t in tokens)
    if bucket:
        clauses.append("bucket = ?")
        params.append(bucket)
    if query:
        clauses.append("(text LIKE ? OR mechanism LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM hooks {where} ORDER BY (status LIKE '%验证%' AND status NOT LIKE '%待%') DESC, id "
        f"LIMIT ?",
        params + [max(1, min(limit, 200))],
    ).fetchall()
    return [dict(row) for row in rows]


def get_doc(conn, uid: str, with_content: bool = True) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM docs WHERE uid=?", (uid,)).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM docs WHERE title LIKE ? LIMIT 1", (f"%{uid}%",)).fetchone()
    if not row:
        return None
    doc = doc_row_to_dict(row, with_content=with_content)
    doc["id"] = row["id"]
    doc["abs_path"] = row["abs_path"]
    return doc


def list_docs(
    conn,
    limit: int = 30,
    offset: int = 0,
    order: str = "recent",
    **filters: Any,
) -> Dict[str, Any]:
    where, params = _build_filters({k: v for k, v in filters.items() if v not in (None, "", [])})
    order_sql = {
        "recent": "d.indexed_at DESC",
        "grade": "CASE d.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, d.id DESC",
        "title": "d.title ASC",
    }.get(order, "d.indexed_at DESC")
    total = conn.execute(f"SELECT COUNT(*) FROM docs d WHERE 1=1 {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM docs d WHERE 1=1 {where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        params + [max(1, min(limit, 200)), max(0, offset)],
    ).fetchall()
    return {"total": total, "items": [doc_row_to_dict(r) for r in rows]}


def nearby_docs(conn, doc_id: int, region: Optional[str], limit: int = 5) -> List[Dict[str, Any]]:
    """同一地区、同一酒店或同类型的兄弟文档，用于阅读页侧栏。"""
    row = conn.execute("SELECT region, hotel, doc_type FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return []
    rows = conn.execute(
        """
        SELECT * FROM docs
        WHERE id <> ? AND (
            (? <> '' AND hotel = ?) OR
            (? <> '' AND region = ? AND doc_type = ?)
        )
        ORDER BY CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, id DESC
        LIMIT ?
        """,
        (doc_id, row["hotel"] or "", row["hotel"] or "", row["region"] or "",
         row["region"] or "", row["doc_type"] or "", limit),
    ).fetchall()
    return [doc_row_to_dict(r) for r in rows]


def facets(conn) -> Dict[str, Any]:
    def group(column: str, limit: int = 40):
        return [
            {"value": r[0], "count": r[1]}
            for r in conn.execute(
                f"SELECT {column}, COUNT(*) FROM docs WHERE {column} IS NOT NULL AND {column} <> '' "
                f"GROUP BY {column} ORDER BY COUNT(*) DESC LIMIT ?",
                (limit,),
            )
        ]

    return {
        "regions": group("region"),
        "doc_types": group("doc_type"),
        "grades": group("grade"),
        "groups": group("group_name"),
        "hotels": group("hotel", 60),
    }
