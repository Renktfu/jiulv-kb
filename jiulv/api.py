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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (
    __version__,
    annotate as annotate_mod,
    compose as compose_mod,
    connectors as connectors_mod,
    db,
    graph as graph_mod,
    inbox as inbox_mod,
    search as search_mod,
    sync as sync_mod,
)
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


class SyncRequest(BaseModel):
    force: bool = Field(False, description="指纹没变也强制重扫")
    source: Optional[str] = Field(None, description="只同步某个来源")


class InboxRequest(BaseModel):
    """agent 写完稿回写进库，下一篇组稿就能拿它当参考。"""

    title: str = Field(..., description="标题")
    content: str = Field(..., description="正文 markdown")
    kind: str = Field("note", description="note/hook/rule/fact/guide/ops/daily/index")
    hotel: str = ""
    region: str = ""
    grade: str = Field("", description="S/A/B，不知道就留空")
    author: str = ""
    agent: str = Field("", description="哪个 agent 写的：openclaw / workbuddy / codex / deepseek")
    note: str = Field("", description="这篇为什么这么写，一句话")
    metrics: Dict[str, int] = Field(default_factory=dict, description="views/likes/saves/comments")


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

    def _fresh_config() -> Config:
        """连接/同步会改 sources.yaml，所以每次用前重读磁盘，并就地更新同一个实例，
        这样闭包里引用 config 的旧端点也会立刻看到新来源。"""
        fresh = load_config(config_path)
        if fresh.data != config.data:
            config.data = fresh.data
        return config

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
        # 收录条数 vs 不同内容：同一篇被几个来源各收一份时两者差得很远
        data["unique_docs"] = search_mod.count_unique_docs(conn)
        return data

    @app.get("/api/v1/sources", tags=["基础"], dependencies=guard)
    def sources(conn=Depends(get_conn)) -> Dict[str, Any]:
        rows = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
        indexed = {r["name"]: dict(r) for r in rows}
        state = sync_mod.read_state(config)
        items = []
        for src in config.sources:
            entry = indexed.get(src["name"], {})
            items.append(
                {
                    "name": src["name"],
                    "group": src.get("group", ""),
                    "path": src["path"],
                    "exists": Path(src["path"]).exists(),
                    "doc_count": entry.get("doc_count", 0),
                    "last_scan": entry.get("last_scan", ""),
                    "generated": bool((state.get("sources") or {}).get(src["name"])),
                }
            )
        return {
            "items": items,
            "last_sync": state.get("last_sync", ""),
            "total_docs": sum(i["doc_count"] for i in items),
        }

    @app.get("/api/v1/connectors", tags=["连接"], dependencies=guard)
    def connectors_api() -> Dict[str, Any]:
        """本机有哪些库、连了哪些、各多少篇。不写文件。"""
        return connectors_mod.status(_fresh_config())

    @app.post("/api/v1/connect", tags=["连接"], dependencies=guard)
    def connect_api(
        deep: bool = Query(False, description="连历史项目库"),
        only: Optional[str] = Query(None, description="只连指定 profile，逗号分隔"),
        sync: bool = Query(False, description="连上后立刻同步"),
    ) -> Dict[str, Any]:
        fresh = _fresh_config()
        picked = [p.strip() for p in (only or "").split(",") if p.strip()] or None
        result = connectors_mod.connect(fresh, deep=deep, only=picked)
        _fresh_config()  # 让 sources.yaml 的新条目立刻生效
        if sync and result["connected"]:
            result["sync"] = sync_mod.sync_once(config, force=True)
        return result

    @app.post("/api/v1/sync", tags=["连接"], dependencies=guard)
    def sync_api(req: SyncRequest) -> Dict[str, Any]:
        """增量同步。指纹没变会直接返回 skipped=true。"""
        return sync_mod.sync_once(_fresh_config(), force=req.force, only_source=req.source)

    @app.get("/api/v1/inbox", tags=["连接"], dependencies=guard)
    def inbox_list(limit: int = 20) -> Dict[str, Any]:
        return {"items": inbox_mod.recent(config, limit=limit)}

    @app.post("/api/v1/inbox", tags=["连接"], dependencies=guard)
    def inbox_save(req: InboxRequest) -> Dict[str, Any]:
        """回写一篇稿子进库，立刻可被检索到。"""
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        try:
            return inbox_mod.save(_fresh_config(), **payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        dedupe: bool = Query(True, description="同一内容被多个来源收录时只留一份（默认开）"),
        region: Optional[str] = None,
        doc_type: Optional[str] = None,
        hotel: Optional[str] = None,
        group: Optional[str] = None,
        grade: Optional[str] = None,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        return search_mod.list_docs(
            conn, limit=limit, offset=offset, order=order, dedupe=dedupe,
            region=region, doc_type=doc_type, hotel=hotel, group=group, grade=grade,
        )

    @app.get("/api/v1/docs/{uid}", tags=["检索"], dependencies=guard)
    def get_doc(uid: str, conn=Depends(get_conn)) -> Dict[str, Any]:
        doc = search_mod.get_doc(conn, uid)
        if not doc:
            raise HTTPException(status_code=404, detail="找不到这篇文档")
        row = conn.execute("SELECT id FROM docs WHERE uid=?", (uid,)).fetchone()
        if row:
            doc["related"] = search_mod.nearby_docs(conn, row["id"], doc.get("region"))
            # 注解与双向链接：这篇为什么值得学、谁还讲过同一个东西
            doc["annotations"] = annotate_mod.for_doc(conn, row["id"])
            doc["concepts"] = graph_mod.doc_concepts(conn, row["id"])
            doc["backlinks"] = graph_mod.backlinks(conn, row["id"])
            doc["duplicates"] = max(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM docs WHERE content_hash=(SELECT content_hash FROM docs WHERE id=?)",
                    (row["id"],),
                ).fetchone()[0]
                - 1,
            )
        return doc

    @app.get("/api/v1/docs/{uid}/raw", tags=["检索"], dependencies=guard)
    def get_doc_raw(uid: str, conn=Depends(get_conn)) -> PlainTextResponse:
        doc = search_mod.get_doc(conn, uid)
        if not doc:
            raise HTTPException(status_code=404, detail="找不到这篇文档")
        return PlainTextResponse(doc.get("content") or "", media_type="text/markdown; charset=utf-8")

    # ------------------------------------------------------------ 图谱

    @app.get("/api/v1/graph", tags=["图谱"], dependencies=guard)
    def graph_api(
        focus: Optional[str] = Query(
            None,
            description="焦点：`hotel:三亚保利瑰丽` / `pain:P2` / `c:12` / `d:88`。"
            "不传则返回概念全景图（节点是概念，边是共同出现的笔记数）",
        ),
        limit: int = 26,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        if not focus:
            data = graph_mod.overview(conn, limit=max(10, min(limit * 2, 120)))
        else:
            data = graph_mod.subgraph(conn, focus, limit=max(4, min(limit, 60)))
        data["node_count"] = len(data["nodes"])
        data["edge_count"] = len(data["edges"])
        return data

    @app.get("/api/v1/concepts", tags=["图谱"], dependencies=guard)
    def concepts(
        kind: Optional[str] = Query(None, description="region/hotel/author/series/audience/pain/hook/technique"),
        limit: int = 40,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        if kind:
            items = graph_mod.by_kind(conn, kind, limit=max(1, min(limit, 200)))
        else:
            items = [
                {"kind": r["kind"], "count": r["n"]}
                for r in conn.execute(
                    "SELECT kind, COUNT(*) n FROM concepts GROUP BY kind ORDER BY n DESC"
                )
            ]
        return {"items": items, "kinds": graph_mod.CONCEPT_KINDS}

    @app.get("/api/v1/concept/docs", tags=["图谱"], dependencies=guard)
    def concept_docs(
        ref: str = Query(..., description="概念引用，例如 hotel:三亚保利瑰丽 或 c:12"),
        limit: int = 50,
        offset: int = 0,
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        return graph_mod.concept_docs(conn, ref, limit=max(1, min(limit, 200)), offset=offset)

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

    # ------------------------------------------------------------ 工具网关

    @app.get("/api/v1/tools", tags=["工具"], dependencies=guard)
    def tools_api(format: str = Query("openai", pattern="^(openai|mcp)$")) -> Any:
        """工具 schema。`openai` 是 function calling 那一套，`mcp` 是原始 inputSchema。

        和 `jiulv tools` 命令同一份数据，只是走 HTTP，方便远端脚本现取现用。
        """
        from .mcp_server import TOOLS

        if format == "mcp":
            return TOOLS
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
            for tool in TOOLS
        ]

    @app.post("/api/v1/tool/{name}", tags=["工具"], dependencies=guard)
    def call_tool(name: str, args: Optional[Dict[str, Any]] = Body(default=None)) -> JSONResponse:
        """按 MCP 的工具名直接执行，返回结构与 MCP 一模一样。

        给「只认 function calling、不接 MCP」的框架用：schema 从 `/api/v1/tools` 取，
        模型给出工具名和 arguments 之后，把 arguments 原样 POST 到这里即可。

        中间不做「工具名 → REST 端点」的映射，是因为那层映射每加一个工具就要改一次，
        而且两边的返回结构迟早对不上（比如 kb_style_rules 在 MCP 里还附带组稿底线，
        映射到 /api/v1/rules 就拿不到）。
        """
        from .mcp_server import Server

        handler = Server(config)
        try:
            data = handler.dispatch(name, args or {})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"没有这个工具：{name}") from exc
        except Exception as exc:  # 工具内部失败也按 200 返回原因，别把框架的循环打断
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse(data)

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
