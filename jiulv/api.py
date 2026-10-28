"""REST API：给网页、Agent、自建脚本共用的一套接口。

所有能力收敛到 6 个端点：检索、读文档、取钩子、取规则、组稿、回填数据。
OpenAPI 文档自动挂在 /docs，任何 Agent 都能读 schema 自己拼调用。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, compose as compose_mod, db, search as search_mod
from .config import Config, load_config
from .taxonomy import load_taxonomy

WEB_DIR = Path(__file__).with_name("web")


# ---------------------------------------------------------------- 请求模型


class ComposeRequest(BaseModel):
    hotel: str = Field("", description="酒店名，例如 三亚文华东方")
    region: str = Field("", description="地区，例如 海南 / 云贵川 / 长白山")
    persona: str = Field("", description="人群，可用 M1-M3/C1/C2/F1 或中文，例如 带娃")
    pain: str = Field("", description="痛点，可用 P1-P7 或中文，例如 选房")
    hook_type: str = Field("", description="钩子类型：反常识/决策瘫痪/代价警示/身份代入/时间窗口/数字具体")
    goal: str = Field("save", description="期望动作：save/comment/consult/share 或中文")
    platform: str = Field("小红书")
    angle: str = Field("", description="本篇切入角度，自由填写")
    style: str = Field("", description="风格档案名")
    extra_facts: List[str] = Field(default_factory=list, description="你手里有、但库里没有的补充事实")
    max_references: int = Field(0, ge=0, le=20)


class FeedbackRequest(BaseModel):
    kind: str = Field("compose", description="compose 或 doc")
    ref: str = Field("", description="组稿 id 或文档 uid")
    title: str = ""
    hotel: str = ""
    region: str = ""
    hook_type: str = ""
    audience: str = ""
    views: int = 0
    likes: int = 0
    saves: int = 0
    comments: int = 0
    follows: int = 0
    is_hit: Optional[bool] = None
    note: str = ""


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="通常直接传 /compose 返回的 prompt")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def create_app(config_path: str | None = None) -> FastAPI:
    config = load_config(config_path)
    db_path = config.db_path
    if not db_path.exists():
        conn = db.open_db(db_path)
        conn.close()

    app = FastAPI(
        title="酒旅爆款知识库 API",
        version=__version__,
        description=(
            "把爆款笔记、写作规则、钩子、房型资料变成可检索、可组稿的服务。\n\n"
            "鉴权：请求头 `X-API-Key: <key>`，或 `Authorization: Bearer <key>`。"
            "本机访问默认免鉴权。"
        ),
    )

    origins = config.get("security", "cors_origins", default=["*"]) or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_conn():
        conn = db.open_db(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def auth(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None),
    ) -> None:
        keys = config.get("security", "api_keys", default=[]) or []
        if not keys:
            return
        provided = x_api_key or ""
        if not provided and authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if provided in keys:
            return
        allow_local = bool(config.get("security", "allow_localhost_without_key", default=True))
        host = request.client.host if request.client else ""
        if allow_local and host in ("127.0.0.1", "::1", "localhost"):
            return
        raise HTTPException(status_code=401, detail="缺少或错误的 API Key")

    guard = [Depends(auth)]

    # ------------------------------------------------------------ 基础

    @app.get("/api/v1/health", tags=["基础"])
    def health(conn=Depends(get_conn)) -> Dict[str, Any]:
        docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        return {
            "ok": True,
            "version": __version__,
            "docs": docs,
            "db": str(db_path),
            "semantic": bool(config.get("semantic", "enabled", default=False)),
            "sources": [s["name"] for s in config.sources],
        }

    @app.get("/api/v1/stats", tags=["基础"], dependencies=guard)
    def stats(conn=Depends(get_conn)) -> Dict[str, Any]:
        data = db.stats(conn)
        data["facets"] = search_mod.facets(conn)
        return data

    @app.get("/api/v1/sources", tags=["基础"], dependencies=guard)
    def sources(conn=Depends(get_conn)) -> Dict[str, Any]:
        rows = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
        return {"items": [dict(r) for r in rows]}

    @app.get("/api/v1/taxonomy", tags=["基础"], dependencies=guard)
    def taxonomy() -> Dict[str, Any]:
        return load_taxonomy(config.get("taxonomy"))

    # ------------------------------------------------------------ 检索

    @app.get("/api/v1/search", tags=["检索"], dependencies=guard)
    def search(
        q: str = Query(..., description="检索词，中文两字以上都能命中"),
        region: Optional[str] = None,
        doc_type: Optional[str] = None,
        hotel: Optional[str] = None,
        group: Optional[str] = None,
        grade: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        mode: str = Query("auto", description="auto/keyword/semantic/hybrid"),
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        return search_mod.search(
            conn, q, config=config, limit=limit, offset=offset, mode=mode,
            region=region, doc_type=doc_type, hotel=hotel,
            group=group, grade=grade, source=source,
        )

    @app.get("/api/v1/docs", tags=["检索"], dependencies=guard)
    def list_docs(
        limit: int = 30,
        offset: int = 0,
        order: str = "recent",
        region: Optional[str] = None,
        doc_type: Optional[str] = None,
        hotel: Optional[str] = None,
        group: Optional[str] = None,
        grade: Optional[str] = None,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        return search_mod.list_docs(
            conn, limit=limit, offset=offset, order=order, region=region,
            doc_type=doc_type, hotel=hotel, group=group, grade=grade,
        )

    @app.get("/api/v1/docs/{uid}", tags=["检索"], dependencies=guard)
    def get_doc(uid: str, conn=Depends(get_conn)) -> Dict[str, Any]:
        doc = search_mod.get_doc(conn, uid)
        if not doc:
            raise HTTPException(status_code=404, detail="找不到这篇文档")
        row = conn.execute("SELECT id FROM docs WHERE uid=?", (uid,)).fetchone()
        if row:
            doc["related"] = search_mod.nearby_docs(conn, row["id"], doc.get("region"))
        return doc

    @app.get("/api/v1/docs/{uid}/raw", tags=["检索"], dependencies=guard)
    def get_doc_raw(uid: str, conn=Depends(get_conn)) -> PlainTextResponse:
        doc = search_mod.get_doc(conn, uid)
        if not doc:
            raise HTTPException(status_code=404, detail="找不到这篇文档")
        return PlainTextResponse(doc.get("content") or "", media_type="text/markdown; charset=utf-8")

    # ------------------------------------------------------------ 结构化素材

    @app.get("/api/v1/hooks", tags=["结构化"], dependencies=guard)
    def hooks(
        hook_type: Optional[str] = None,
        audience: Optional[str] = None,
        pain: Optional[str] = None,
        bucket: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 30,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        items = search_mod.list_hooks(
            conn, hook_type=hook_type, audience=audience, pain=pain,
            bucket=bucket, query=q, limit=limit,
        )
        return {"total": len(items), "items": items}

    @app.get("/api/v1/rules", tags=["结构化"], dependencies=guard)
    def rules(limit: int = 5, conn=Depends(get_conn)) -> Dict[str, Any]:
        rows = conn.execute(
            "SELECT * FROM docs WHERE doc_type='rule' ORDER BY char_count DESC LIMIT ?", (limit,)
        ).fetchall()
        return {
            "items": [
                {
                    "uid": r["uid"],
                    "title": r["title"],
                    "summary": r["summary"],
                    "char_count": r["char_count"],
                    "path": r["rel_path"],
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------ 组稿

    @app.post("/api/v1/compose", tags=["组稿"], dependencies=guard)
    def compose(req: ComposeRequest, conn=Depends(get_conn)) -> Dict[str, Any]:
        brief = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        return compose_mod.compose(conn, config, brief)

    @app.post("/api/v1/generate", tags=["组稿"], dependencies=guard)
    def generate(req: GenerateRequest) -> Dict[str, Any]:
        try:
            return compose_mod.generate(config, req.prompt)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------ 数据回收

    @app.post("/api/v1/feedback", tags=["回收"], dependencies=guard)
    def feedback(req: FeedbackRequest, conn=Depends(get_conn)) -> Dict[str, Any]:
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        cur = conn.execute(
            "INSERT INTO outcomes(kind, ref, title, hotel, region, hook_type, audience, views, likes,"
            " saves, comments, follows, is_hit, note, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                payload["kind"], payload["ref"], payload["title"], payload["hotel"],
                payload["region"], payload["hook_type"], payload["audience"],
                payload["views"], payload["likes"], payload["saves"], payload["comments"],
                payload["follows"],
                None if payload["is_hit"] is None else int(bool(payload["is_hit"])),
                payload["note"], _now(),
            ),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}

    @app.get("/api/v1/outcomes", tags=["回收"], dependencies=guard)
    def outcomes(limit: int = 50, conn=Depends(get_conn)) -> Dict[str, Any]:
        rows = conn.execute(
            "SELECT * FROM outcomes ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
        ).fetchall()
        items = [dict(r) for r in rows]
        by_hook: Dict[str, Dict[str, Any]] = {}
        for item in items:
            key = item.get("hook_type") or "未标注"
            bucket = by_hook.setdefault(key, {"hook_type": key, "count": 0, "hits": 0, "likes": 0, "saves": 0})
            bucket["count"] += 1
            bucket["hits"] += int(item.get("is_hit") or 0)
            bucket["likes"] += item.get("likes") or 0
            bucket["saves"] += item.get("saves") or 0
        return {"items": items, "by_hook_type": list(by_hook.values())}

    # ------------------------------------------------------------ 静态页面

    if WEB_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        icon = WEB_DIR / "favicon.svg"
        if icon.exists():
            return FileResponse(str(icon), media_type="image/svg+xml")
        return JSONResponse({}, status_code=404)

    app.state.config = config
    app.state.db_path = db_path
    return app


# uvicorn jiulv.api:app
app = create_app(os.environ.get("JIULV_CONFIG"))
