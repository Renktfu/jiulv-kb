# 酒旅爆款知识库（jiulv-kb）

给做酒店代订、酒旅内容的人用的一套本地知识库：把散在硬盘里的爆款笔记、写作规则、开头钩子、房型资料收进一个库，用一句话检索，再一键拼成可以直接投喂给任何大模型的写作上下文包。

不联网也能跑，不依赖云服务，一个 SQLite 文件就是全部数据。检索和组稿能力通过 REST 与 MCP 两套接口开放，OpenClaw、DeepSeek Harness、Codex、WorkBuddy 都能直接接。

## 它解决什么问题

做酒旅代订的人，素材通常是这样的：几千篇笔记躺在文件夹里，规则写在某个 md 里，钩子记在备忘录，房型信息在某份 docx。真要写一篇稿，先翻半小时资料，再凭记忆写，写完还得自查有没有编。

这套库把这件事拆成三步，每步都有对应的接口：

1. 查得到。关键词检索命中到段落，返回标题、等级、赞藏评和原文片段，附上文件路径方便核对。
2. 看得懂。每篇爆款旁边直接列出「为什么这么写」：该写什么、必须保留什么意见、数据说明了什么。注解全部从原文小节和采集记录里抽出来，不生成、不润色。
3. 连得起来。地区、酒店、作者、人群、痛点、钩子、手法都是图谱里的节点，点一个酒店能看到所有写过它的笔记，点一篇笔记能看到谁还讲过同一个东西。
4. 写得对。组稿接口按酒店、地区、人群、痛点、钩子类型，把风格规则、同地区爆款结构、可改写的钩子、可核对事实拼成一份带输出约束的提示词。
5. 能复盘。发布后回填数据，按钩子类型统计哪类有效，下一轮据此调。

## 30 秒跑起来

```bash
git clone <你的仓库地址> jiulv-kb
cd jiulv-kb
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[parsers]"

jiulv init                    # 生成 config.yaml
jiulv connect                 # 探测本机的 WorkBuddy / OpenCLaw 资料库，写进 sources.yaml
jiulv start                   # 一条命令：建索引 + 起网页 + 后台同步，并自动开浏览器
```

`jiulv start` 就是日常那一条。缺索引它会先建（1500 篇约半分钟），已经在跑就只把浏览器
再打开一次，端口被别的程序占了会自动往后顺延一个，不会甩一段 address already in use
给你。服务关掉窗口就停，数据仍在 `data/kb.sqlite3` 里。

不想开终端有两个更省事的入口：macOS 双击 `scripts/jiulv-start.command`，Windows 双击
`scripts/jiulv-start.bat`。两个脚本只负责找到 `python`，然后把活交给 `scripts/setup.py`：
**没有 .venv 就自己建，缺依赖就自己装**，装不上才说话，而且说的是「该装什么」而不是一段
`ModuleNotFoundError`。所以别人拿到这个文件夹，只要机器上有 Python，双击就能用，不用先教
他敲 pip。

想开机就自动跑起来：

```bash
jiulv autostart on      # macOS 写 LaunchAgent，Linux 写 systemd 用户服务，Windows 建计划任务
jiulv autostart status  # 装没装、在不在跑、日志在哪
jiulv autostart off     # 卸掉，一条命令，不留残渣
```

自启用的命令是 `start --no-browser`：开机自动弹一堆标签页是骚扰不是便利。日志写在
`data/logs/jiulv.log`。都不需要管理员权限。

`[parsers]` 是 docx/pdf/xlsx 的解析依赖。不装也能跑，只是这几种格式会被跳过。

资料不在默认位置的话，`jiulv connect --dry-run` 先看它探测到了什么，再用
`JIULV_WB_ROOT` / `JIULV_OC_ROOT` 把根目录指过去；或者直接在 `config.yaml` 里手写
`sources`，手写的永远优先。接法细节见 [`docs/CONNECT.md`](docs/CONNECT.md)。

出问题先跑 `jiulv doctor`：版本、路径、依赖、编码、每个来源在不在、库能不能读，
一条命令全打出来，比逐项猜快。

## 文档

先读哪份，看你要解决什么。

[`docs/USAGE.md`](docs/USAGE.md) 使用说明。怎么启动、界面上每一块是干什么的、写一篇稿的七步顺序、命令行全部命令、资料怎么放、备份与换机、常见问题。**第一次用读这份。**

[`docs/LLM.md`](docs/LLM.md) 接到大模型上。三种接法（MCP、HTTP + function calling、OpenAPI 导入）分别怎么配，十一个工具各在什么时候调，可以直接复制的系统提示词，排错清单。想让 Claude、Cursor、Codex、DeepSeek 用上这套库，读这份。

[`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) 四端速查。熟手复制配置用，详细说明都在 LLM.md。

[`docs/CONNECT.md`](docs/CONNECT.md) 怎么把散在硬盘上的资料库连进来，探测规则和手写覆盖。

[`docs/API.md`](docs/API.md) 接口定义，每个端点的参数、返回、容易用错的地方。

[`docs/INGEST.md`](docs/INGEST.md) 收录规则，目录怎么起名、等级怎么标、钩子七字段怎么写。

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 数据结构与设计取舍，想改代码先看这份。

[`docs/WINDOWS.md`](docs/WINDOWS.md) Windows 交接文档，写给「在 Windows 上接手这个仓库的人或 AI」。

### Windows

同一套代码在 Windows 上也能跑，但有几个只在 Windows 上才发作的坑（控制台代码页、
路径大小写、目录联结、文件名非法字符、长路径），都已经处理掉了。安装步骤和排查
清单见 [`docs/WINDOWS.md`](docs/WINDOWS.md)，那份文档是写给「在 Windows 上接手
这个仓库的人或 AI」看的，包含每个平台敏感点的文件位置和改坏后的症状。

要点三条：日常双击 `scripts/jiulv-start.bat` 最省事，命令行里则用
`python -m jiulv.cli <子命令>` 而不是裸 `jiulv`，避免 PATH 问题；
项目目录不要放在 OneDrive 同步盘里（SQLite 的 WAL 三个文件会错位）；
路径很深时先开 `LongPathsEnabled`。

## 打包发给别人

自己用 clone 就行。要给别人（尤其是 Windows 用户），别让他装 Python 环境、别让他 clone，
直接打一个包：

```bash
python3 scripts/make_dist.py           # 生成 dist/
python3 scripts/make_dist.py --clean   # 先清空再生成
```

打出来是这样：

```
dist/
├── 先读我.txt            两个平台各一份，先看这个
├── macos/                苹果电脑用这份，双击「双击启动.command」
│   ├── 双击启动.command
│   ├── jiulv/ docs/ tests/ scripts/ examples/
│   ├── pyproject.toml README.md LICENSE ...
│   └── data/             空的，第一次启动把索引建在这
└── windows/              Windows 用这份，双击「双击启动.bat」
    └── （同上，启动器换成 .bat，另附使用说明）
```

两个目录**完全独立**，可以只拷其中一个走。

刻意的取舍：包里不带 `.venv`（虚拟环境里全是绝对路径，换目录就废）、不带
`data/kb.sqlite3`（160MB 真实索引，含客户酒店名，且和你本机路径绑死）、不带
`config.yaml` / `sources.yaml`（里面是本机绝对路径，没有它们反而好——启动时会按
当前机器的实际布局重新探测）。所以包只有 0.5MB，换台机器、换个目录都能直接跑。

`使用说明.txt` 是 UTF-8 带 BOM + CRLF 写的。看着像多余，但不加会出事：老版记事本
按 ANSI 打开无 BOM 的 UTF-8 中文 txt 是满屏乱码，用的人第一反应是「文件坏了」；
只给 LF 的换行，1809 之前的记事本会把整段并成一行。Windows 那个 `.bat` 同理，
cmd 的老解析器对孤立 LF 有已知问题，打包时统一转 CRLF。

## 界面上有什么

六个页签，都是给写稿这件事服务的：

检索。左边按地区、类型、等级、来源筛，右边是结果。点开进右侧阅读器，第一眼是这篇的注解，往下才是原文。

图谱。像 Obsidian 那样的关系图，但节点不只笔记：地区、酒店、作者、人群、痛点、钩子、手法全是一等节点。默认看全景（哪些概念互相连着），点任意节点钻进它的一跳子图，点笔记节点直接打开原文。可拖拽、可滚轮缩放。

组稿台。填酒店、地区、人群、痛点、钩子类型，点一下拿到完整提示词，一键复制。这是最常用的一页。

钩子库。所有结构化钩子按类型筛，每条带原文、适用人群、对应痛点、心理机制、期望动作。

来源。每个资料库连在哪、库里多少篇、上次什么时候同步，右上角一个按钮手动同步一次。
这一页存在的意义是让库不是黑盒——看不到来源，就不知道答案的出处。

数据。文档数、段落数、注解数、概念数、类型分布、来源分布，以及回填的发布数据。

## 注解是怎么来的

每篇笔记里其实已经写好了「为什么这么写」，只是没人把它当数据。这里有三个来源，全部原样搬运：

- 笔记自身的 `####` 小节：预订启示、提取要点、优点、缺点与避坑点、评论区、房型分析。文末那句「写文案时：公区夸到顶，房间要有保留意见」权重最高，因为它是写资料的人当时留下的结论。
- 每日采集记录里每条后面的长点评，也就是 `——` 之后那段。它直接说这篇的价值在哪、哪儿不能当事实用，是全库信息密度最高的注解。
- 赞藏评比例和标题用词，按规则算出来的判断。这类会标成「规则推算」，跟原文摘录分开，不混为一谈。

打开一篇 S 级爆款，通常能看到十几条这样的注解，按重要性排序，前三条直接摊开，其余折叠。每条都带原文引用和来源文件，点得回去对账。

## 图谱连的是什么

不是把所有笔记两两连起来，那样只会得到一团毛线。连边只有一种：笔记 ↔ 概念。

所以「三亚保利瑰丽」这个节点下面，挂的是库里斯所有提过它的笔记；点开其中一篇，反向链接会告诉你「三亚瑞吉最全选房指南」跟它共享了 4 个概念（同痛点 P2、同手法「工具承诺」、同地区海南）。这才是真正能拿来用的关联——不是长得像，是讲的同一件事。

同一篇笔记常在多份快照里各存一份，图谱按「标题 + 类型」折叠，只留一份，其余不计入度数，否则一个酒店下面会挂四个同名节点。

## 接 Agent

三条路，选一条。完整步骤和每个客户端的配置在 [`docs/LLM.md`](docs/LLM.md)：MCP（客户端支持就走这条，最省事）、HTTP + function calling（自己写代码，或框架只认 function calling）、OpenAPI 导入（Dify、Coze、GPTs 这类云端平台）。

先跑这条，把要填的绝对路径和可直接复制的配置打印出来：

```bash
.venv/bin/python -m jiulv.cli where
```

工具一共十一个：`kb_search`、`kb_get_doc`、`kb_annotations`、`kb_graph`、`kb_hooks`、`kb_style_rules`、`kb_context_pack`、`kb_stats`、`kb_feedback`、`kb_sync`、`kb_save`。

`kb_annotations` 把一个概念下所有笔记的注解汇总去重（写某家酒店前调一次，等于把库里关于它的判断全摊在桌上），`kb_graph` 用来顺着概念找相关的东西，`kb_get_doc` 返回的也不只是原文，还带着这篇的注解和所属概念。

`kb_sync` 和 `kb_save` 是闭环的两头：前者把两个资料库的新增同步进来，后者把 agent 写完的稿子连同「为什么这么写」回写进库。写完就存档，下一篇组稿它已经在参考里了。

不接 MCP 的框架走这一个端点，返回结构和 MCP 完全一致，不用自己把工具名映射成 REST：

```
POST /api/v1/tool/{工具名}     请求体就是工具的参数对象
GET  /api/v1/tools             取 function calling 的 schema
```

样例都在 `examples/` 下，字段含义和放置位置见 `docs/LLM.md`：

- `examples/workbuddy/mcp.json` → `~/.workbuddy/mcp.json`，改完要在连接器管理页点「信任」
- `examples/openclaw/mcp.json` → OpenClaw 的 mcpServers 配置
- `examples/codex/config.toml` → `~/.codex/config.toml` 的 `[mcp_servers.jiulv]`
- `examples/function-calling/agent_loop.py` → 完整的 function calling 循环，任何 OpenAI 兼容模型能跑
- `examples/openapi/jiulv-mini.yaml` → 给 Dify / Coze / GPTs 导入的精简 OpenAPI
- `examples/deepseek-harness/tools.json` → 只认 function calling 的框架直接用这份 schema
- `examples/http/client.py` → 不接 MCP，直接调 REST

接上之后的典型一句话：

> 用 kb_context_pack 给三亚仁恒皇冠写一篇给带娃家庭的选房笔记，钩子用反常识。

Agent 会自己取规则、挑钩子、找同地区爆款结构，再按提示词里的约束写。

## 接口一览

完整定义看服务启动后的 `/docs`（自动生成的 OpenAPI）。常用的是这些：

- `GET /api/v1/search?q=三亚 亲子&region=海南&limit=10` 检索，`mode` 可选 `keyword`／`semantic`／`hybrid`／`auto`
- `GET /api/v1/docs/{uid}` 取一篇全文，附带注解、所属概念、反向链接、同酒店相关篇目
- `GET /api/v1/graph?focus=hotel:三亚保利瑰丽` 图谱；不传 `focus` 返回概念全景
- `GET /api/v1/concept/docs?ref=pain:P2` 某个概念下的全部笔记
- `GET /api/v1/hooks?hook_type=反常识&audience=M2` 取结构化钩子
- `POST /api/v1/compose` 组稿，返回 `prompt` 与用到的资料清单
- `POST /api/v1/feedback` 回填发布数据
- `GET /api/v1/stats` 库的规模与分布
- `GET /api/v1/sources` 连了哪些资料库、各多少篇、上次同步时间
- `GET /api/v1/connectors` 本机探测到哪些库（含没连的）
- `POST /api/v1/sync` 增量同步，指纹没变会返回 `skipped: true`
- `POST /api/v1/inbox` 回写一篇稿子进库，立刻可检索
- `GET /api/v1/tools` 工具 schema，`format=openai` 是 function calling 那套，`format=mcp` 是原始 inputSchema
- `POST /api/v1/tool/{name}` 按工具名直接执行，返回结构与 MCP 一致，给不接 MCP 的框架用
- `GET /api/v1/inbox` 回写池最近条目

鉴权是请求头 `X-API-Key` 或 `Authorization: Bearer`。`security.api_keys` 留空时本机访问不需要密钥，适合单机使用；要给团队共用就配上密钥，并把 `allow_localhost_without_key` 关掉。

## 资料怎么放

库不强制目录结构，但认一些约定，用来判断类型、地区、等级。写对了，检索和组稿会准很多。详见 `docs/INGEST.md`，核心就几条：

- 目录名带 `13_钩子库`、`04_风格规则`、`07_硬资料`、`02_地区素材库` 这类前缀，会自动归类
- 文件名前缀 `S_`／`A_`／`B_` 或正文里写「爆款等级：S级」，会记成爆款等级
- 正文里的 `- **标题**：`、`- **地区**：`、`- **互动数据**：`、`- **作者**：` 会被抽成字段
- `- **地区**：海南·三亚·文华东方` 这种三段式会拆出地区与酒店

钩子文件按七字段写（`## H-xxx-001` 开头，跟 `- 原文：`、`- 类型：`、`- 人群：`、`- 痛点：`、`- 机制：`、`- 转化：`、`- 状态：`），收录时会进结构化表，组稿时能按人群和痛点精确取。

## 检索是怎么做的

中文检索最容易踩的坑是分词。SQLite 自带的 unicode61 不切中文，trigram 又要求查询至少三个字，「三亚」「瑞吉」这种两字查询直接空手而归。

这里的做法是自己做一层二元分词：`三亚文华东方` 切成 `三亚 亚文 文华 华东 东方`，建索引和查询用同一个函数。好处是不用词典、不用联网、两字查询稳定命中，代价是索引比原文大一半左右。

排序用 BM25，标题列加权，索引类文件降权（那种文件动辄列几百个标题，很容易把相关性带偏），S／A 级爆款加权。同一篇笔记如果在多份快照里各存了一份，结果里按标题折叠，只显示分数最高那份并标注「另有 N 份副本」，原始文件一个都不动。

语义检索是可选的第二路，走 OpenAI 兼容的 `/v1/embeddings`，两路结果用 RRF 融合。没配就不启用，全部功能照常。

## 目录结构

```
jiulv/            后端
  tokenizer.py    中文二元分词
  parsers.py      md/txt/docx/pdf/xlsx 解析
  extract.py      元数据抽取、分块、片段挑选
  ingest.py       扫描与建索引（增量）
  search.py       BM25 / 向量 / RRF 混合
  annotate.py     注解抽取：小节、采集点评、指标推算
  graph.py        概念图：概念抽取、双向链接、子图查询
  connectors.py   连接层：探测两个资料库、生成 sources.yaml
  sync.py         指纹增量同步与常驻监听
  inbox.py        回写池：agent 的产出入库
  platform_compat.py 跨平台差异：控制台编码、路径同一性、原子写、文件名、遍历
  doctor.py       环境自检（jiulv doctor）
  launcher.py     启动编排（端口顺延、缺库先建）+ 三平台开机自启

scripts/          双击启动：jiulv-start.command（macOS）/ jiulv-start.bat（Windows）
  setup.py        启动前的自举：没有 .venv 就建，缺依赖就装，两个平台共用
  make_dist.py    打分成包：生成 dist/macos 与 dist/windows
  compose.py      组稿上下文包
  api.py          FastAPI 路由
  mcp_server.py   MCP stdio server（纯标准库）
  cli.py          命令行
  web/            前端（零构建，原生 JS）
docs/             使用说明、接入大模型、架构、接口、收录、连接方式、Windows 交接
examples/         各端接入样例（MCP 配置、function calling 循环、OpenAPI）
tests/            101 个测试，跑 pytest 即可
```

## 设计取舍

本地优先。数据只有一个 sqlite 文件，备份就是拷文件，离线可用。

少依赖。核心只依赖 fastapi、uvicorn、pyyaml；解析和向量都是可选项。MCP server 用纯标准库实现，不绑官方 SDK，装不装 SDK 都能接。

不编造。组稿时如果库里没有这家酒店，会明确警告「只有同地区参考，写房型和服务前必须先补采集」；提示词里也会写死「资料里没有的一律不写」。宁可写少，不写错。

可解释。每条参考都带来源路径和入选理由（同酒店／同地区／关键词兜底），写完能逐条核对。注解也分两种出处：原文摘录的标「原文摘录」，规则推出来的标「规则推算」，一眼能分清哪句是资料里写的、哪句是算出来的。

## 常见问题

**收录会动我的原文件吗。** 不会。只读，所有写入都在 `data/kb.sqlite3` 和自己的 `config.yaml` 里。

**资料更新了怎么办。** 再跑一次 `jiulv ingest`。按修改时间判断，没变的直接跳过，1500 篇大约两秒；删掉的文件会同步从索引里移除。

**能不能只用命令行。** 可以，`jiulv search "三亚 亲子"`、`jiulv compose --hotel 三亚瑞吉 --persona M2`，加 `--json` 出结构化结果。

**能不能给多人用。** `jiulv serve --host 0.0.0.0` 配好 `security.api_keys`，前面再套一层反向代理。

## 贡献

欢迎提 issue 和 PR。跑测试：`pytest --basetemp=.pytest-tmp`。新增解析格式、改进中文排序、补钩子类型都很有价值。

提交前请确认：不把真实客户资料、真实笔记原文放进仓库（`.gitignore` 已挡住 `corpus/` 和 `data/*.sqlite3`）。

## 许可

MIT。
