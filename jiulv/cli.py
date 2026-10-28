"""命令行入口：jiulv init / ingest / search / compose / serve / mcp / stats。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__, db
from .config import load_config

PKG_ROOT = Path(__file__).resolve().parent.parent


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir or ".").expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    cfg = target / "config.yaml"
    example = PKG_ROOT / "config.example.yaml"
    if cfg.exists() and not args.force:
        print(f"config.yaml 已存在：{cfg}（加 --force 覆盖）")
    else:
        if example.exists():
            shutil.copyfile(example, cfg)
            print(f"已生成配置：{cfg}")
        else:
            print("找不到 config.example.yaml，请从仓库根目录复制")
            return 1
    (target / "data").mkdir(exist_ok=True)
    print("下一步：编辑 config.yaml 里的 sources 路径，然后运行  jiulv ingest")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import ingest

    config = load_config(args.config)
    if not config.sources:
        print("配置里没有 sources。先运行 jiulv init，并填好资料库路径。")
        return 1
    conn = db.open_db(config.db_path)
    report = ingest(
        config,
        conn,
        rebuild=args.rebuild,
        only_source=args.source,
        verbose=args.verbose or args.quiet is False,
        prune=not args.no_prune,
        logger=lambda msg: print(msg, flush=True) if not args.quiet else None,
    )
    conn.close()
    _print(report.as_dict())
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    from .embeddings import build_index

    config = load_config(args.config)
    if not config.get("semantic", "enabled", default=False) and not args.force:
        print("语义检索未启用（config.yaml 里 semantic.enabled: false）。加 --force 可强制跑。")
        return 1
    conn = db.open_db(config.db_path)
    try:
        count = build_index(conn, config, logger=None if args.quiet else print, limit=args.limit)
    except Exception as exc:
        print(f"向量化失败：{exc}")
        return 1
    finally:
        conn.close()
    _print({"embedded": count})
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from . import search as search_mod

    config = load_config(args.config)
    conn = db.open_db(config.db_path)
    result = search_mod.search(
        conn, args.query, config=config, limit=args.limit, mode=args.mode,
        region=args.region, doc_type=args.type, hotel=args.hotel,
    )
    conn.close()
    if args.json:
        _print(result)
        return 0
    print(f"命中 {result['total']} 篇（{result['mode']}）")
    for hit in result["hits"]:
        doc = hit["doc"]
        stat = "/".join(
            f"{int(v)}{k}" for k, v in
            [("赞", doc["metrics"].get("likes", 0)), ("藏", doc["metrics"].get("saves", 0)),
             ("评", doc["metrics"].get("comments", 0))] if v
        )
        print(f"\n[{doc['grade'] or '-'}级] {doc['title']}  {stat}")
        print(f"  {doc['source']} · {doc['path']}")
        if hit["snippet"]:
            print(f"  {hit['snippet'][:150]}")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    from . import compose as compose_mod

    config = load_config(args.config)
    conn = db.open_db(config.db_path)
    brief = {
        "hotel": args.hotel or "",
        "region": args.region or "",
        "persona": args.persona or "",
        "pain": args.pain or "",
        "hook_type": args.hook or "",
        "goal": args.goal or "save",
        "angle": args.angle or "",
        "platform": args.platform or "小红书",
    }
    result = compose_mod.compose(conn, config, brief)
    conn.close()
    if args.json:
        _print(result)
    else:
        if result["warnings"]:
            print("提醒：")
            for warning in result["warnings"]:
                print(f"- {warning}")
            print()
        print(result["prompt"])
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from . import search as search_mod

    config = load_config(args.config)
    if not config.db_path.exists():
        print("还没有索引，先运行 jiulv ingest")
        return 1
    conn = db.open_db(config.db_path)
    data = db.stats(conn)
    data["facets"] = search_mod.facets(conn)
    conn.close()
    _print(data)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    config = load_config(args.config)
    host = args.host or config.get("app", "host", default="127.0.0.1")
    port = int(args.port or config.get("app", "port", default=8787))
    application = create_app(args.config)
    print(f"网页界面： http://{host}:{port}/")
    print(f"接口文档： http://{host}:{port}/docs")
    uvicorn.run(application, host=host, port=port, log_level=args.log_level)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve

    config = load_config(args.config)
    serve(config)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    conn = db.open_db(config.db_path)
    out = Path(args.out or "data/export").expanduser()
    out.mkdir(parents=True, exist_ok=True)
    docs_path = out / "docs.jsonl"
    count = 0
    with open(docs_path, "w", encoding="utf-8") as fh:
        for row in conn.execute("SELECT * FROM docs"):
            record = {k: row[k] for k in row.keys()}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    hooks_path = out / "hooks.jsonl"
    with open(hooks_path, "w", encoding="utf-8") as fh:
        for row in conn.execute("SELECT * FROM hooks"):
            fh.write(json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False) + "\n")
    conn.close()
    _print({"docs": str(docs_path), "count": count, "hooks": str(hooks_path)})
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    """导出工具 schema，给不做 MCP、只认 function calling 的框架用。"""
    from .mcp_server import TOOLS

    if args.format == "openai":
        functions = [
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
        _print(functions)
    else:
        _print(TOOLS)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jiulv", description="酒旅爆款知识库")
    parser.add_argument("--config", help="配置文件路径，默认找当前目录 config.yaml")
    parser.add_argument("--version", action="version", version=f"jiulv-kb {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="生成 config.yaml 和 data 目录")
    p_init.add_argument("dir", nargs="?", help="目标目录，默认当前目录")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="扫描资料库并建索引")
    p_ingest.add_argument("--rebuild", action="store_true", help="清空重建")
    p_ingest.add_argument("--source", help="只处理某个来源")
    p_ingest.add_argument("--no-prune", action="store_true", help="不删除已消失的文件")
    p_ingest.add_argument("--verbose", "-v", action="store_true")
    p_ingest.add_argument("--quiet", "-q", action="store_true")
    p_ingest.set_defaults(func=cmd_ingest)

    p_embed = sub.add_parser("embed", help="为段落生成向量（需配置嵌入接口）")
    p_embed.add_argument("--limit", type=int, default=0)
    p_embed.add_argument("--force", action="store_true")
    p_embed.add_argument("--quiet", "-q", action="store_true")
    p_embed.set_defaults(func=cmd_embed)

    p_search = sub.add_parser("search", help="命令行检索")
    p_search.add_argument("query")
    p_search.add_argument("--region")
    p_search.add_argument("--type", dest="type")
    p_search.add_argument("--hotel")
    p_search.add_argument("--limit", type=int, default=8)
    p_search.add_argument("--mode", default="auto", choices=["auto", "keyword", "semantic", "hybrid"])
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_compose = sub.add_parser("compose", help="生成组稿上下文包")
    p_compose.add_argument("--hotel")
    p_compose.add_argument("--region")
    p_compose.add_argument("--persona", help="M1-M3/C1/C2/F1 或中文")
    p_compose.add_argument("--pain", help="P1-P7 或中文")
    p_compose.add_argument("--hook")
    p_compose.add_argument("--goal", default="save")
    p_compose.add_argument("--angle")
    p_compose.add_argument("--platform", default="小红书")
    p_compose.add_argument("--json", action="store_true")
    p_compose.set_defaults(func=cmd_compose)

    p_stats = sub.add_parser("stats", help="索引统计")
    p_stats.set_defaults(func=cmd_stats)

    p_serve = sub.add_parser("serve", help="启动网页与接口服务")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--log-level", default="info")
    p_serve.set_defaults(func=cmd_serve)

    p_mcp = sub.add_parser("mcp", help="以 stdio MCP server 方式运行")
    p_mcp.set_defaults(func=cmd_mcp)

    p_tools = sub.add_parser("tools", help="导出工具 schema（mcp / openai function calling）")
    p_tools.add_argument("--format", default="openai", choices=["openai", "mcp"])
    p_tools.set_defaults(func=cmd_tools)

    p_export = sub.add_parser("export", help="导出 docs/hooks 为 jsonl")
    p_export.add_argument("--out")
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
