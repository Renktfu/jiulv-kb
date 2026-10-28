"""语义检索的可选实现：OpenAI 兼容 /v1/embeddings + 余弦相似度。

默认关闭。开启需要配一个 embeddings 接口（DeepSeek、OpenAI、本地 vLLM 都行）。
向量存在 SQLite 里，numpy 装了就用矩阵乘，没装就用纯 Python 遍历，几千到几万
段落都能接受。
"""

from __future__ import annotations

import array
import json
import math
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

from .config import Config

_CACHE: Dict[str, Tuple[List[int], List[List[float]]]] = {}


class EmbeddingError(RuntimeError):
    pass


def _api_key(config: Config) -> str:
    env_name = config.get("semantic", "api_key_env", default="JIULV_EMBED_API_KEY")
    key = os.environ.get(env_name or "", "")
    if not key:
        raise EmbeddingError(
            f"没有找到嵌入模型 API Key，请设置环境变量 {env_name}，"
            "或在 config.yaml 的 semantic.api_key_env 指定变量名。"
        )
    return key


def embed_texts(config: Config, texts: Sequence[str]) -> List[List[float]]:
    base_url = str(config.get("semantic", "base_url", default="")).rstrip("/")
    model = config.get("semantic", "model", default="")
    if not base_url:
        raise EmbeddingError("semantic.base_url 未配置")
    payload = json.dumps({"model": model, "input": list(texts)}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/embeddings",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key(config)}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise EmbeddingError(f"嵌入接口返回 {exc.code}：{detail}") from exc
    except Exception as exc:
        raise EmbeddingError(f"嵌入接口调用失败：{exc}") from exc

    items = data.get("data") or []
    vectors = [item.get("embedding") for item in sorted(items, key=lambda x: x.get("index", 0))]
    if not vectors or vectors[0] is None:
        raise EmbeddingError("嵌入接口没有返回向量")
    return vectors  # type: ignore[return-value]


def pack(vec: Sequence[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack(blob: bytes) -> List[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


def build_index(conn, config: Config, logger=None, limit: Optional[int] = None) -> int:
    model = config.get("semantic", "model", default="")
    batch_size = int(config.get("semantic", "batch_size", default=32))
    sql = (
        "SELECT c.id, c.text FROM chunks c "
        "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ? "
        "WHERE e.chunk_id IS NULL ORDER BY c.doc_id, c.seq"
    )
    params: Tuple = (model,)
    if limit:
        sql += " LIMIT ?"
        params = (model, limit)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return 0

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        vectors = embed_texts(config, [r["text"][:2000] for r in batch])
        for row, vec in zip(batch, vectors):
            conn.execute(
                "INSERT OR REPLACE INTO embeddings(chunk_id, model, dim, vec) VALUES(?,?,?,?)",
                (row["id"], model, len(vec), pack(vec)),
            )
        conn.commit()
        total += len(batch)
        if logger:
            logger(f"  向量化 {total}/{len(rows)}")
    return total


def load_matrix(conn, model: str):
    docs: List[str] = []
    rows = conn.execute(
        "SELECT chunk_id, dim, vec FROM embeddings WHERE model=? ORDER BY chunk_id", (model,)
    ).fetchall()
    ids: List[int] = []
    vectors: List[List[float]] = []
    for row in rows:
        vec = unpack(row["vec"])
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        ids.append(row["chunk_id"])
        vectors.append([v / norm for v in vec])
    return ids, vectors


def _normalized_rows(conn, model: str):
    key = f"{id(conn)}:{model}"
    cached = _CACHE.get(key)
    count = conn.execute("SELECT COUNT(*) FROM embeddings WHERE model=?", (model,)).fetchone()[0]
    if cached and len(cached[0]) == count:
        return cached
    ids, vectors = load_matrix(conn, model)
    _CACHE[key] = (ids, vectors)
    return _CACHE[key]


def semantic_search(conn, config: Config, query: str, top_k: int = 40) -> List[Tuple[int, float]]:
    """返回 [(chunk_id, 相似度)]，相似度 0-1。"""
    model = config.get("semantic", "model", default="")
    if not model:
        return []
    try:
        query_vec = embed_texts(config, [query])[0]
    except EmbeddingError:
        return []

    norm = math.sqrt(sum(v * v for v in query_vec)) or 1.0
    query_vec = [v / norm for v in query_vec]
    ids, vectors = _normalized_rows(conn, model)
    if not ids:
        return []

    try:
        import numpy as np  # type: ignore

        matrix = np.asarray(vectors, dtype="float32")
        scores = matrix @ np.asarray(query_vec, dtype="float32")
        order = scores.argsort()[::-1][:top_k]
        return [(ids[i], float(scores[i])) for i in order]
    except ImportError:
        scored = [
            (chunk_id, sum(a * b for a, b in zip(query_vec, vec)))
            for chunk_id, vec in zip(ids, vectors)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def invalidate_cache() -> None:
    _CACHE.clear()
