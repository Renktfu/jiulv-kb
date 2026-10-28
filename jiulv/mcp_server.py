"""MCP server（stdio）：让 OpenClaw / Codex / WorkBuddy / DeepSeek Harness 直接调库。

只用标准库实现 JSON-RPC 2.0，不依赖官方 mcp SDK，方便任何环境一行命令接上：

    jiulv mcp

约定：日志一律走 stderr，stdout 只输出协议内容，避免污染管道。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional

from . import (
    __version__,
    annotate as annotate_mod,
    compose as compose_mod,
    db,
    graph as graph_mod,
    search as search_mod,
)
from .config import Config, load_config
from .platform_compat import read_stdio_lines, write_stdio_line
from .taxonomy import load_taxonomy

PROTOCOL_VERSION = "2024-11-05"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "kb_search",
        "description": (
            "检索酒旅文案知识库。库里是爆款笔记、写作规则、钩子、房型与政策资料。"
            "返回标题、等级、互动数据、命中片段。写任何酒店文案前先调这个，确认资料是否存在。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，中文两字以上即可，如「三亚 亲子 选房」"},
                "region": {"type": "string", "description": "地区过滤：海南 / 云贵川 / 西安 / 长白山 / 新疆 / 崇礼 / 东南亚"},
                "doc_type": {
                    "type": "string",
                    "description": "类型过滤：note 爆款笔记 / rule 写作规则 / hook 钩子 / guide 房型攻略 / fact 硬资料 / ops 代运营 / daily 每日采集",
                },
                "hotel": {"type": "string", "description": "酒店名过滤"},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_get_doc",
        "description": "按 uid 或标题关键词取一篇文档的全文，用于核对房型、服务、评论等具体事实。",
        "inputSchema": {
            "type": "object",
            "properties": {"uid_or_title": {"type": "string"}},
            "required": ["uid_or_title"],
        },
    },
    {
        "name": "kb_annotations",
        "description": "把一个概念（酒店/地区/人群/痛点/手法）下所有笔记的注解汇总去重。"
        "返回「必须写的保留意见」「可写的卖点」「预订启示」「采集点评」。"
        "写某家酒店前先调一次，等于把库里关于它的判断全摊在桌上。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "概念引用，例如 hotel:三亚保利瑰丽、region:海南、pain:P2",
                },
                "kinds": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 24},
            },
            "required": ["focus"],
        },
    },
    {
        "name": "kb_graph",
        "description": "知识图谱：不传 focus 返回概念全景（哪些地区/酒店/痛点互相连着），"
        "传 focus 返回该节点的一跳邻居。用来找「还有哪些相关的东西」，不是用来检索正文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "hotel:xxx / region:xxx / pain:P2 / d:<uid>"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "kb_hooks",
        "description": "取结构化开头钩子。每条含原文、类型、适用人群、对应痛点、心理机制、期望动作。写标题和第一句时用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hook_type": {"type": "string", "description": "反常识 / 决策瘫痪 / 代价警示 / 身份代入 / 时间窗口 / 数字具体"},
                "audience": {"type": "string", "description": "M1 M2 M3 C1 C2 F1，可用空格分隔多个"},
                "pain": {"type": "string", "description": "P1-P7"},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
            },
        },
    },
    {
        "name": "kb_style_rules",
        "description": "取写作规则总库与风格范文要点。动笔前先读，违反规则的稿子会被判不合格。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "想了解的规则主题，如「价格」「房型描写」「避坑」"},
                "limit": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
            },
        },
    },
    {
        "name": "kb_context_pack",
        "description": (
            "一次拿全写稿素材：规则 + 钩子 + 同地区爆款结构 + 可核对事实，拼成一段可直接使用的提示词。"
            "这是本库的主出口，写整篇笔记前优先调它，再按返回的 prompt 生成正文。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hotel": {"type": "string", "description": "酒店名"},
                "region": {"type": "string", "description": "地区"},
                "persona": {"type": "string", "description": "人群：M1-M3/C1/C2/F1 或中文如「带娃」"},
                "pain": {"type": "string", "description": "痛点：P1-P7 或中文如「选房」"},
                "hook_type": {"type": "string", "description": "钩子类型"},
                "goal": {"type": "string", "description": "期望动作：save/comment/consult/share"},
                "angle": {"type": "string", "description": "本篇切入角度"},
                "extra_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "你手上另有、库里没有的事实，一并写进提示词",
                },
            },
        },
    },
    {
        "name": "kb_stats",
        "description": "库的规模与分布：文档数、地区分布、类型分布、钩子数。判断某地区资料够不够写之前先看这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "kb_feedback",
        "description": "回填发布后的数据（赞藏评），用于复盘哪些钩子有效。发完稿就调一次。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "hotel": {"type": "string"},
                "region": {"type": "string"},
                "hook_type": {"type": "string"},
                "audience": {"type": "string"},
                "likes": {"type": "integer", "default": 0},
                "saves": {"type": "integer", "default": 0},
                "comments": {"type": "integer", "default": 0},
                "views": {"type": "integer", "default": 0},
                "is_hit": {"type": "boolean", "description": "是否达到你的爆款线"},
                "note": {"type": "string"},
            },
        },
    },
    {
        "name": "kb_sync",
        "description": "把 WorkBuddy / OpenCLaw 资料库的新增与改动同步进库。很久没搜到新素材时调一次；"
        "指纹没变会直接返回 skipped=true，代价很低。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False, "description": "无变化也强制重扫"},
                "source": {"type": "string", "description": "只同步某个来源名"},
            },
        },
    },
    {
        "name": "kb_save",
        "description": "把写完的稿子回写进库，立刻可被检索到。一篇稿子写完后调一次，库会随产出一起变厚。"
        "note 一栏写清「这篇为什么这么写」，下一篇组稿会用到。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "正文 markdown"},
                "kind": {
                    "type": "string",
                    "default": "note",
                    "description": "note 笔记 / hook 钩子 / rule 规则 / fact 硬资料 / guide 房型攻略 / ops 代运营",
                },
                "hotel": {"type": "string"},
                "region": {"type": "string"},
                "grade": {"type": "string", "description": "S/A/B，不确定就留空"},
                "agent": {"type": "string", "description": "谁写的：openclaw / workbuddy / codex"},
                "note": {"type": "string", "description": "为什么这么写，一句话"},
                "metrics": {"type": "object", "description": "views/likes/saves/comments，发布后再补"},
            },
            "required": ["title", "content"],
        },
    },
]


class Server:
    def __init__(self, config: Config):
        self.config = config

    # ------------------------------------------------------------ 工具实现

    def _conn(self):
        return db.open_db(self.config.db_path)

    def tool_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            result = search_mod.search(
                conn,
                args.get("query", ""),
                config=self.config,
                limit=int(args.get("limit") or 8),
                region=args.get("region"),
                doc_type=args.get("doc_type"),
                hotel=args.get("hotel"),
            )
            return {
                "mode": result["mode"],
                "total": result["total"],
                "hits": [
                    {
                        "uid": hit["doc"]["uid"],
                        "title": hit["doc"]["title"],
                        "type": hit["doc"]["doc_type"],
                        "grade": hit["doc"]["grade"],
                        "region": hit["doc"]["region"],
                        "hotel": hit["doc"]["hotel"],
                        "metrics": hit["doc"]["metrics"],
                        "snippet": hit["snippet"],
                        "path": hit["doc"]["path"],
                    }
                    for hit in result["hits"]
                ],
            }
        finally:
            conn.close()

    def tool_get_doc(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            doc = search_mod.get_doc(conn, args.get("uid_or_title", ""))
            if not doc:
                return {"found": False, "message": "库里没有这篇，先补采集，不要凭空写"}
            row = conn.execute("SELECT id FROM docs WHERE uid=?", (doc["uid"],)).fetchone()
            if row:
                # 注解一起带上：Agent 拿到的不只是原文，还有「这篇为什么值得学」
                doc["annotations"] = [
                    {"kind": a["kind"], "label": a["label"], "text": a["text"],
                     "evidence": a["evidence"], "origin": a["origin"]}
                    for a in annotate_mod.for_doc(conn, row["id"], limit=12)
                ]
                doc["concepts"] = [
                    {"kind": c["kind"], "label": c["label"]} for c in graph_mod.doc_concepts(conn, row["id"])[:8]
                ]
            return {"found": True, "doc": doc}
        finally:
            conn.close()

    def tool_annotations(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """把一个概念下所有笔记的注解汇总去重。

        写某家酒店之前调一次，等于把库里关于它的「必须写的保留意见」「可写的卖点」
        「预订启示」全摊在桌上，不用逐篇翻。
        """
        conn = self._conn()
        try:
            ref = args.get("focus") or ""
            kinds = args.get("kinds") or ["takeaway", "con", "booking", "pro", "review"]
            limit = int(args.get("limit") or 24)
            data = graph_mod.concept_docs(conn, ref, limit=80)
            if not data["concept"]:
                return {
                    "found": False,
                    "message": f"图谱里没有这个节点：{ref}。先用 kb_graph 不传参数看有哪些概念。",
                }
            items: List[Dict[str, Any]] = []
            seen = set()
            for doc in data["items"]:
                row = conn.execute("SELECT id FROM docs WHERE uid=?", (doc["uid"],)).fetchone()
                if not row:
                    continue
                for ann in annotate_mod.for_doc(conn, row["id"]):
                    if ann["kind"] not in kinds:
                        continue
                    key = ann["text"][:36]
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({**ann, "from": doc["title"], "uid": doc["uid"]})
            items.sort(key=lambda item: -item["weight"])
            return {
                "found": True,
                "focus": data["concept"]["label"],
                "docs": data["total"],
                "total": len(items),
                "items": items[:limit],
            }
        finally:
            conn.close()

    def tool_graph(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """图谱导航：不传 focus 看全景，传了就取这个节点的一跳邻居。"""
        conn = self._conn()
        try:
            focus = args.get("focus") or ""
            limit = int(args.get("limit") or 20)
            data = (
                graph_mod.subgraph(conn, focus, limit=max(4, min(limit, 30)))
                if focus
                else graph_mod.overview(conn, limit=max(10, min(limit, 40)))
            )
            # Agent 的上下文比屏幕贵，把 note 这类长字段去掉
            for node in data["nodes"]:
                node.pop("note", None)
            return data
        finally:
            conn.close()

    def tool_hooks(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            items = search_mod.list_hooks(
                conn,
                hook_type=args.get("hook_type"),
                audience=args.get("audience"),
                pain=args.get("pain"),
                limit=int(args.get("limit") or 8),
            )
            return {"total": len(items), "items": items}
        finally:
            conn.close()

    def tool_rules(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            topic = args.get("topic") or "文案 规则"
            limit = int(args.get("limit") or 3)
            hits = search_mod.search(
                conn, topic, config=self.config, limit=limit, mode="keyword", doc_type="rule"
            )
            items = [
                {
                    "uid": hit["doc"]["uid"],
                    "title": hit["doc"]["title"],
                    "snippet": hit["snippet"],
                    "path": hit["doc"]["path"],
                }
                for hit in hits["hits"]
            ]
            if not items:
                rows = conn.execute(
                    "SELECT uid, title, summary, rel_path FROM docs WHERE doc_type='rule' "
                    "ORDER BY char_count DESC LIMIT ?", (limit,)
                ).fetchall()
                items = [
                    {"uid": r["uid"], "title": r["title"], "snippet": r["summary"], "path": r["rel_path"]}
                    for r in rows
                ]
            return {"items": items, "guardrails": load_taxonomy().get("guardrails", [])}
        finally:
            conn.close()

    def tool_context_pack(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            brief = {
                "hotel": args.get("hotel", ""),
                "region": args.get("region", ""),
                "persona": args.get("persona", ""),
                "pain": args.get("pain", ""),
                "hook_type": args.get("hook_type", ""),
                "goal": args.get("goal", "save"),
                "angle": args.get("angle", ""),
                "extra_facts": args.get("extra_facts") or [],
            }
            return compose_mod.compose(conn, self.config, brief)
        finally:
            conn.close()

    def tool_stats(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._conn()
        try:
            data = db.stats(conn)
            data["facets"] = search_mod.facets(conn)
            # 和 /api/v1/stats 保持同一口径：docs 是收录条数，unique_docs 是去掉各来源
            # 副本之后的内容数。走 MCP 的 Agent 和走 HTTP 的脚本看到的数要一样。
            data["unique_docs"] = search_mod.count_unique_docs(conn)
            return data
        finally:
            conn.close()

    def tool_feedback(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from datetime import datetime, timezone

        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO outcomes(kind, ref, title, hotel, region, hook_type, audience, views,"
                " likes, saves, comments, follows, is_hit, note, created_at)"
                " VALUES('mcp','',?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (
                    args.get("title", ""), args.get("hotel", ""), args.get("region", ""),
                    args.get("hook_type", ""), args.get("audience", ""),
                    int(args.get("views") or 0), int(args.get("likes") or 0),
                    int(args.get("saves") or 0), int(args.get("comments") or 0),
                    None if args.get("is_hit") is None else int(bool(args.get("is_hit"))),
                    args.get("note", ""),
                    datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid}
        finally:
            conn.close()

    def tool_sync(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from . import sync as sync_mod

        result = sync_mod.sync_once(
            self.config,
            force=bool(args.get("force")),
            only_source=args.get("source") or None,
        )
        # 报告里 by_type 之类对 agent 没用，只留规模变化
        report = result.get("report") or {}
        if report:
            result["report"] = {
                "added": report.get("added", 0),
                "updated": report.get("updated", 0),
                "removed": report.get("removed", 0),
                "annotations": report.get("annotations", 0),
                "duration_s": report.get("duration_s", 0),
            }
        return result

    def tool_save(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from . import inbox as inbox_mod

        try:
            result = inbox_mod.save(
                self.config,
                title=args.get("title", ""),
                content=args.get("content", ""),
                kind=args.get("kind", "note"),
                hotel=args.get("hotel", ""),
                region=args.get("region", ""),
                grade=args.get("grade", ""),
                agent=args.get("agent", ""),
                note=args.get("note", ""),
                metrics=args.get("metrics") or {},
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result.pop("report", None)
        return result

    def dispatch(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        table: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "kb_search": self.tool_search,
            "kb_get_doc": self.tool_get_doc,
            "kb_annotations": self.tool_annotations,
            "kb_graph": self.tool_graph,
            "kb_hooks": self.tool_hooks,
            "kb_style_rules": self.tool_rules,
            "kb_context_pack": self.tool_context_pack,
            "kb_stats": self.tool_stats,
            "kb_feedback": self.tool_feedback,
            "kb_sync": self.tool_sync,
            "kb_save": self.tool_save,
        }
        handler = table.get(name)
        if not handler:
            raise KeyError(f"未知工具：{name}")
        return handler(args or {})

    # ------------------------------------------------------------ 协议

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        def ok(result: Any) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}

        def err(code: int, text: str) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

        if method == "initialize":
            return ok(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "jiulv-kb", "version": __version__},
                    "instructions": (
                        "酒旅爆款文案知识库。写酒店笔记前先调 kb_context_pack 拿规则+钩子+参考+事实，"
                        "或先 kb_search 确认资料是否存在。资料里没有的不要编。"
                    ),
                }
            )
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name", "")
            try:
                data = self.dispatch(name, params.get("arguments") or {})
            except KeyError as exc:
                return err(-32601, str(exc))
            except Exception as exc:  # 工具内部错误也按协议返回，不要崩掉进程
                return ok({"content": [{"type": "text", "text": f"调用失败：{exc}"}], "isError": True})
            return ok(
                {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
                    "isError": False,
                }
            )
        if method in ("resources/list", "prompts/list"):
            return ok({"resources": []} if method == "resources/list" else {"prompts": []})
        return err(-32601, f"未实现的方法：{method}")


def serve(config: Optional[Config] = None) -> None:
    """stdio 上的 JSON-RPC 主循环。

    Windows 上必须按字节读写，不能直接用 sys.stdin / print：
    stdin 默认按代码页（cp936/cp437）解码，客户端送来的 UTF-8 中文会变成乱码，
    检索词错了还不报错；stdout 的文本模式又会把 \n 翻成 \r\n。
    这两件事都不报异常，只是「结果不对」，比崩掉更难查。
    """
    config = config or load_config()
    if not config.db_path.exists():
        print("提示：数据库还不存在，先运行 jiulv ingest", file=sys.stderr)
    server = Server(config)
    print(f"jiulv-kb MCP server 已启动（stdio），库：{config.db_path}", file=sys.stderr)

    for raw in read_stdio_lines():
        line = raw.strip().lstrip("\ufeff")  # 有些客户端会带 BOM
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            write_stdio_line(json.dumps(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON 解析失败"}}
            ))
            continue
        if isinstance(message, list):
            responses = [r for r in (server.handle(m) for m in message) if r]
            if responses:
                write_stdio_line(json.dumps(responses, ensure_ascii=False))
            continue
        response = server.handle(message)
        if response is not None:
            write_stdio_line(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    serve()
