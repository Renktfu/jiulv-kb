"""命令行入口：init / connect / sync / watch / start / ingest / search / compose /
serve / autostart / mcp / stats / sources / doctor / export。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__, db
from .config import load_config
from .platform_compat import setup_console

PKG_ROOT = Path(__file__).resolve().parent.parent

# pip 装成 wheel 之后，仓库根目录的 config.example.yaml 不会跟着进包，
# 这时候就地生成一份等价的最小配置：Windows 用户多半是 pip 装的，不能让他卡在这。
CONFIG_TEMPLATE = """\
# jiulv-kb 配置。所有路径支持 ~ 展开和 ${ENV_VAR} 变量。

app:
  name: 酒旅爆款知识库
  database: data/kb.sqlite3
  host: 127.0.0.1
  port: 8787

# jiulv connect 探测到的资料库会写进同目录的 sources.yaml，不覆盖这个文件。
# 这里手写的 sources 永远优先；sources.yaml 里的条目加 enabled: false 可停用。
connect:
  watch_interval: 120

# 收录来源。每个 source 是一个目录，递归扫描 include 里的格式。
# 先跑 `jiulv connect` 自动探测，探测不到再在这里手写：
# sources:
#   - name: 我的文案
#     group: mine
#     path: D:/资料/我的文案
#     include: ["**/*.md", "**/*.docx", "**/*.pdf"]
#     exclude: ["**/~$*"]
sources: []

search:
  max_limit: 50
  fallback_to_or: true
  title_weight: 6.0
  snippet_chars: 220

semantic:
  enabled: false
  base_url: https://api.deepseek.com/v1
  api_key_env: JIULV_EMBED_API_KEY
  model: text-embedding-3-small
  batch_size: 32
  weight: 0.45

generation:
  enabled: false
  base_url: https://api.deepseek.com/v1
  api_key_env: JIULV_LLM_API_KEY
  model: deepseek-chat
  temperature: 0.85
  max_tokens: 1600

security:
  api_keys: []
  allow_localhost_without_key: true
  cors_origins: ["*"]

compose:
  max_references: 6
  excerpt_chars: 600
  default_style: 瑞克克
"""


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir or ".").expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    cfg = target / "config.yaml"
    example = PKG_ROOT / "config.example.yaml"
    if cfg.exists() and not args.force:
        print(f"config.yaml 已存在：{cfg}（加 --force 覆盖）")
    elif example.exists():
        shutil.copyfile(example, cfg)
        print(f"已生成配置：{cfg}")
    else:
        # pip 装成 wheel 时没有仓库根目录，用内置模板，别让人卡在第一步
        cfg.write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
        print(f"已生成配置（内置模板）：{cfg}")
    (target / "data").mkdir(exist_ok=True)
    print("下一步：jiulv connect 探测本机资料库（或手改 config.yaml 里的 sources 路径），然后 jiulv sync")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    """探测本机的 WorkBuddy / OpenCLaw 资料库，生成 sources.yaml。"""
    from . import connectors

    config = load_config(args.config)
    only = [p.strip() for p in (args.only or "").split(",") if p.strip()] or None
    result = connectors.connect(config, deep=args.deep, only=only, dry_run=args.dry_run)

    if args.json:
        _print(result)
    else:
        picked = result["connected"]
        if picked:
            print(f"新连上 {len(picked)} 个来源：" if not args.dry_run else f"可连 {len(picked)} 个来源：")
            for item in picked:
                print(f"  [{item['group']}] {item['name']}  {item['docs']} 篇  {item['path']}")
        else:
            print("没有新来源可连。")
        if result["skipped"]:
            print("\n跳过：")
            for item in result["skipped"]:
                print(f"  {item['title']}：{item['reason']}")
        if result.get("written"):
            print(f"\n已写入 {result['written']}")
            print("下一步：jiulv sync（或 jiulv watch 常驻监听）")

    if args.sync and result["connected"] and not args.dry_run:
        from . import sync as sync_mod

        fresh = load_config(args.config)
        report = sync_mod.sync_once(
            fresh, force=True, logger=lambda msg: print(msg, flush=True)
        )
        _print({"sync": {k: v for k, v in report.items() if k != "report"},
                "report": report.get("report")})
    return 0 if result["ok"] else 1


def cmd_sync(args: argparse.Namespace) -> int:
    """增量同步：指纹变了才收录。"""
    from . import sync as sync_mod

    config = load_config(args.config)
    result = sync_mod.sync_once(
        config,
        force=args.force,
        only_source=args.source,
        logger=None if args.quiet else (lambda msg: print(msg, flush=True)),
    )
    if args.json:
        _print(result)
        return 0 if result.get("ok") else 1
    if result.get("skipped"):
        print(f"无变化（{result.get('reason')}）")
        return 0
    report = result.get("report") or {}
    print(
        f"已同步：+{report.get('added', 0)} 篇  ~{report.get('updated', 0)} 篇  "
        f"-{report.get('removed', 0)} 篇，注解 {report.get('annotations', 0)} 条，"
        f"耗时 {result.get('duration_s')}s"
    )
    if result.get("changed"):
        print("变化来源：" + "、".join(result["changed"]))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """常驻监听，隔一会儿看一次两个资料库有没有新东西。"""
    from . import sync as sync_mod

    config = load_config(args.config)
    interval = args.interval or int(config.get("connect", "watch_interval", default=120))
    sync_mod.watch(
        config,
        interval=interval,
        logger=lambda msg: print(msg, flush=True),
        max_rounds=1 if args.once else None,
    )
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
        refresh_derived=True if args.refresh_derived else None,
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


def _display_width(text: str) -> int:
    """终端里的显示宽度。中文占两列，用 len() 对齐会歪。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def cmd_sources(args: argparse.Namespace) -> int:
    """列出每个来源：在不在、收了多少篇、上次扫到什么时候。

    迭代时最常问的两个问题——「我新接的目录进来了吗」「这批新笔记落了几个」
    ——有了这张表就不用去翻数据库。
    """
    from . import sync as sync_mod

    config = load_config(args.config)
    state = sync_mod.read_state(config)
    indexed = {}
    if config.db_path.exists():
        conn = db.open_db(config.db_path)
        try:
            indexed = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM sources")}
        finally:
            conn.close()

    items = []
    for src in config.sources:
        row = indexed.get(src["name"], {})
        items.append(
            {
                "name": src["name"],
                "group": src.get("group", ""),
                "path": src["path"],
                "exists": Path(src["path"]).exists(),
                "docs": row.get("doc_count", 0),
                "last_scan": row.get("last_scan", ""),
            }
        )

    if args.json:
        _print({"sources": items, "last_sync": state.get("last_sync", "")})
        return 0

    total = sum(i["docs"] for i in items)
    print(f"{len(items)} 个来源，共 {total} 篇；上次同步 {state.get('last_sync', '未同步')}")
    print()
    width = max((_display_width(i["name"]) for i in items), default=8)
    for item in items:
        flag = "有" if item["exists"] else "缺"
        scan = (item["last_scan"] or "-")[:16].replace("T", " ")
        name = item["name"] + " " * max(0, width - _display_width(item["name"]))
        print(f"  {flag} {item['docs']:>5} 篇  {name}  {scan}  {item['path']}")
    missing = [i["name"] for i in items if not i["exists"]]
    if missing:
        print()
        print("目录不在了：" + "、".join(missing))
        print("路径被移动或改名是最常见的故障，跑 jiulv doctor 看详情。")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境自检。出问题时先跑这个，把输出整段贴出来就能定位。"""
    from . import doctor

    config = load_config(args.config)
    data = doctor.collect(config)
    if args.json:
        _print(data)
    else:
        print(doctor.format_text(data))
    # 有硬性问题（没来源、库读不了）时返回非 0，方便脚本判断
    fatal = (not data["config"]["sources"]) or (
        data["db"]["exists"] and not data["db"].get("readable", True)
    )
    return 1 if fatal else 0


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


def cmd_start(args: argparse.Namespace) -> int:
    """一条命令跑起来：缺索引先建，然后网页 + 后台同步一起开着。"""
    from . import launcher

    config = load_config(args.config)
    return launcher.start(
        config,
        host=args.host,
        port=args.port,
        interval=args.interval,
        browser=not args.no_browser,
        watch_enabled=not args.no_watch,
    )


def cmd_autostart(args: argparse.Namespace) -> int:
    from . import launcher

    config = load_config(args.config)
    if args.action == "status":
        info = launcher.autostart_status(config)
    elif args.action == "on":
        info = launcher.autostart_install(config, dry_run=args.dry_run)
    else:
        info = launcher.autostart_remove(config)

    if args.json:
        _print(info)
        return 0 if info.get("installed", True) or info.get("ok") else 1

    print(f"平台：{info['platform']}    目标：{info.get('target', '')}")
    print(f"已安装：{'是' if info.get('installed') else '否'}    "
          f"在运行：{'是' if info.get('running') else '否'}")
    print(f"命令：{info['command']}")
    print(f"日志：{info['log']}")
    if info.get("detail"):
        print(f"说明：{info['detail']}")
    if info.get("message"):
        print(f"结果：{info['message']}")
    if info.get("would_write"):
        print("—— 将会写入 ——")
        print(info.get("would_body") or info["would_write"])
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


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    """探一下服务在不在。只看能不能连上，不发请求。"""
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_where(args: argparse.Namespace) -> int:
    """打印接入外部客户端时要填的绝对路径，以及可直接复制的配置片段。

    Agent 启动 MCP 子进程时的 PATH 和终端里不一样，配置里写 `jiulv` 经常找不到；
    手写绝对路径又容易抄错。这个命令的作用就是让这一步不用猜。
    片段里一律用 `python -m jiulv.mcp_server` 而不是 `jiulv`：前者不依赖 PATH、
    不依赖 shebang、Windows 上也一样跑，是四端里最不容易出错的一种写法。
    """
    config = load_config(args.config)
    root = PKG_ROOT
    cfg_path = config.path or (Path.cwd() / "config.yaml")
    # 优先项目自带的 .venv：客户端启动子进程时不会继承你终端的激活状态
    candidates = [
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ]
    py = next((p for p in candidates if p.exists()), Path(sys.executable))
    host = config.get("app", "host", default="127.0.0.1")
    port = int(config.get("app", "port", default=8787))
    db_path = config.db_path
    running = _port_open(host, port)

    mcp_json = {
        "mcpServers": {
            "jiulv-kb": {
                "command": str(py),
                "args": ["-m", "jiulv.mcp_server"],
                "env": {"JIULV_CONFIG": str(cfg_path), "PYTHONPATH": str(root)},
            }
        }
    }
    toml = (
        "[mcp_servers.jiulv]\n"
        f"command = '{py}'\n"
        'args = ["-m", "jiulv.mcp_server"]\n'
        "startup_timeout_sec = 20\n\n"
        "[mcp_servers.jiulv.env]\n"
        f"JIULV_CONFIG = '{cfg_path}'\n"
        f"PYTHONPATH = '{root}'\n"
    )

    info = {
        "root": str(root),
        "config": str(cfg_path),
        "database": str(db_path),
        "python": str(py),
        "server": f"http://{host}:{port}",
        "running": running,
        "mcp_json": mcp_json,
        "codex_toml": toml,
    }
    if args.json:
        _print(info)
        return 0

    db_note = f"（{db_path.stat().st_size / 1048576:.0f} MB）" if db_path.exists() else "（还没建）"
    print("把下面这些路径填进客户端的配置里，不要再手抄。\n")
    print(f"项目根目录    {root}")
    print(f"配置文件      {cfg_path}")
    print(f"数据库        {db_path} {db_note}")
    print(f"Python        {py}")
    print(f"服务地址      http://{host}:{port}    {'在线' if running else '未启动'}")
    print("\n—— 这一段贴进 Claude Desktop / Cursor / Cline / Cherry Studio 的 mcpServers ——\n")
    print(json.dumps(mcp_json, ensure_ascii=False, indent=2))
    print("\n—— 这一段追加到 ~/.codex/config.toml ——\n")
    print(toml, end="")
    print("\n—— Claude Code 一条命令 ——\n")
    if sys.platform.startswith("win"):
        print(f'claude mcp add jiulv -e JIULV_CONFIG="{cfg_path}" -e PYTHONPATH="{root}" -- "{py}" -m jiulv.mcp_server')
    else:
        print(f"claude mcp add jiulv -e JIULV_CONFIG={cfg_path} -e PYTHONPATH={root} -- {py} -m jiulv.mcp_server")
    print("\n—— 验证 server 本身通不通（打出版本号和工具数就算通）——\n")
    print("printf '%s\\n' '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \\")
    print(f"  | {py} -m jiulv.mcp_server")
    print("\n打不出来就是 server 侧的问题，跟客户端无关，先修这一步。")
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

    p_connect = sub.add_parser("connect", help="探测本机 WorkBuddy / OpenCLaw 资料库并接入")
    p_connect.add_argument("--dry-run", action="store_true", help="只看探测结果，不写配置")
    p_connect.add_argument("--deep", action="store_true", help="连历史项目库（夹带代码仓库，较慢）")
    p_connect.add_argument("--only", help="只连指定 profile，逗号分隔，例如 wb-library,oc-reference")
    p_connect.add_argument("--sync", action="store_true", help="连上后立刻同步一次")
    p_connect.add_argument("--json", action="store_true")
    p_connect.set_defaults(func=cmd_connect)

    p_sync = sub.add_parser("sync", help="增量同步：资料库有变化才收录")
    p_sync.add_argument("--force", action="store_true", help="指纹没变也强制跑一遍")
    p_sync.add_argument("--source", help="只同步某个来源")
    p_sync.add_argument("--quiet", "-q", action="store_true")
    p_sync.add_argument("--json", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    p_watch = sub.add_parser("watch", help="常驻监听两个资料库，有变化就收录")
    p_watch.add_argument("--interval", type=int, default=0, help="轮询间隔秒数，默认 120")
    p_watch.add_argument("--once", action="store_true", help="只看一轮就退出")
    p_watch.set_defaults(func=cmd_watch)

    p_ingest = sub.add_parser("ingest", help="扫描资料库并建索引")
    p_ingest.add_argument("--rebuild", action="store_true", help="清空重建")
    p_ingest.add_argument("--source", help="只处理某个来源")
    p_ingest.add_argument("--no-prune", action="store_true", help="不删除已消失的文件")
    p_ingest.add_argument("--refresh-derived", action="store_true",
                          help="没有任何文件改动也重算注解与图谱")
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

    p_sources = sub.add_parser("sources", help="列出来源：路径、篇数、上次扫描")
    p_sources.add_argument("--json", action="store_true")
    p_sources.set_defaults(func=cmd_sources)

    p_doctor = sub.add_parser("doctor", help="环境自检：编码、路径、依赖、库状态")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=cmd_doctor)

    p_serve = sub.add_parser("serve", help="启动网页与接口服务")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--log-level", default="info")
    p_serve.set_defaults(func=cmd_serve)

    p_start = sub.add_parser("start", help="一条命令启动：补齐索引 + 网页 + 后台同步")
    p_start.add_argument("--host")
    p_start.add_argument("--port", type=int)
    p_start.add_argument("--interval", type=int, help="后台同步间隔（秒）")
    p_start.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    p_start.add_argument("--no-watch", action="store_true", help="不跑后台同步")
    p_start.set_defaults(func=cmd_start)

    p_auto = sub.add_parser("autostart", help="开机自启：on / off / status")
    p_auto.add_argument("action", choices=["on", "off", "status"])
    p_auto.add_argument("--dry-run", action="store_true", help="只打印要写什么，不落盘")
    p_auto.add_argument("--json", action="store_true")
    p_auto.set_defaults(func=cmd_autostart)

    p_mcp = sub.add_parser("mcp", help="以 stdio MCP server 方式运行")
    p_mcp.set_defaults(func=cmd_mcp)

    p_tools = sub.add_parser("tools", help="导出工具 schema（mcp / openai function calling）")
    p_tools.add_argument("--format", default="openai", choices=["openai", "mcp"])
    p_tools.set_defaults(func=cmd_tools)

    p_export = sub.add_parser("export", help="导出 docs/hooks 为 jsonl")
    p_export.add_argument("--out")
    p_export.set_defaults(func=cmd_export)

    p_where = sub.add_parser("where", help="打印各客户端配置要填的绝对路径与片段")
    p_where.add_argument("--json", action="store_true")
    p_where.set_defaults(func=cmd_where)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # 必须在任何 print 之前：Windows 上输出被重定向时用的是系统代码页，
    # 中文会直接把命令打崩。见 platform_compat.setup_console。
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
