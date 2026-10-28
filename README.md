# 酒旅爆款知识库（jiulv-kb）

给做酒店代订、酒旅内容的人用的一套本地知识库：把散在硬盘里的爆款笔记、写作规则、开头钩子、房型资料收进一个库，用一句话检索，再一键拼成可以直接投喂给任何大模型的写作上下文包。

不联网也能跑，不依赖云服务，一个 SQLite 文件就是全部数据。检索和组稿能力通过 REST 与 MCP 两套接口开放，OpenClaw、DeepSeek Harness、Codex、WorkBuddy 都能直接接。

## 它解决什么问题

做酒旅代订的人，素材通常是这样的：几千篇笔记躺在文件夹里，规则写在某个 md 里，钩子记在备忘录，房型信息在某份 docx。真要写一篇稿，先翻半小时资料，再凭记忆写，写完还得自查有没有编。

这套库把这件事拆成三步，每步都有对应的接口：

1. 查得到。关键词检索命中到段落，返回标题、等级、赞藏评和原文片段，附上文件路径方便核对。
2. 写得对。组稿接口按酒店、地区、人群、痛点、钩子类型，把风格规则、同地区爆款结构、可改写的钩子、可核对事实拼成一份带输出约束的提示词。
3. 能复盘。发布后回填数据，按钩子类型统计哪类有效，下一轮据此调。

## 30 秒跑起来

```bash
git clone <你的仓库地址> jiulv-kb
cd jiulv-kb
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[parsers]"

jiulv init                    # 生成 config.yaml
# 编辑 config.yaml，把 sources 里的 path 改成你的资料库目录
jiulv ingest                  # 建索引，1500 篇大约半分钟
jiulv serve                   # 打开 http://127.0.0.1:8787
```

`[parsers]` 是 docx/pdf/xlsx 的解析依赖。不装也能跑，只是这几种格式会被跳过。

## 界面上有什么

四个页签，都是给写稿这件事服务的：

检索。左边按地区、类型、等级、来源筛，右边是结果。点开进右侧阅读器，看全文、看同酒店的相关篇目。

组稿台。填酒店、地区、人群、痛点、钩子类型，点一下拿到完整提示词，一键复制。这是最常用的一页。

钩子库。所有结构化钩子按类型筛，每条带原文、适用人群、对应痛点、心理机制、期望动作。

数据。文档数、段落数、钩子数、类型分布、来源分布，以及回填的发布数据。

## 接 Agent

四端都能接，走的是同一个 MCP server（stdio），工具名一致。

```bash
jiulv mcp                                  # MCP stdio server
jiulv tools --format openai                # 导出 function calling schema
```

工具一共七个：`kb_search`、`kb_get_doc`、`kb_hooks`、`kb_style_rules`、`kb_context_pack`、`kb_stats`、`kb_feedback`。

各端的配置文件样例在 `examples/` 下，字段含义和放置位置见 `docs/INTEGRATIONS.md`：

- `examples/workbuddy/mcp.json` → `~/.workbuddy/mcp.json`，改完要在连接器管理页点「信任」
- `examples/openclaw/mcp.json` → OpenClaw 的 mcpServers 配置
- `examples/codex/config.toml` → `~/.codex/config.toml` 的 `[mcp_servers.jiulv]`
- `examples/deepseek-harness/tools.json` → 只认 function calling 的框架直接用这份 schema
- `examples/http/client.py` → 不接 MCP，直接调 REST

接上之后的典型一句话：

> 用 kb_context_pack 给三亚仁恒皇冠写一篇给带娃家庭的选房笔记，钩子用反常识。

Agent 会自己取规则、挑钩子、找同地区爆款结构，再按提示词里的约束写。

## 接口一览

完整定义看服务启动后的 `/docs`（自动生成的 OpenAPI）。常用的六个：

- `GET /api/v1/search?q=三亚 亲子&region=海南&limit=10` 检索，`mode` 可选 `keyword`／`semantic`／`hybrid`／`auto`
- `GET /api/v1/docs/{uid}` 取一篇全文，附带同酒店相关篇目
- `GET /api/v1/hooks?hook_type=反常识&audience=M2` 取结构化钩子
- `POST /api/v1/compose` 组稿，返回 `prompt` 与用到的资料清单
- `POST /api/v1/feedback` 回填发布数据
- `GET /api/v1/stats` 库的规模与分布

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
  compose.py      组稿上下文包
  api.py          FastAPI 路由
  mcp_server.py   MCP stdio server（纯标准库）
  cli.py          命令行
  web/            前端（零构建，原生 JS）
docs/             架构、接口、接入、收录约定
examples/         四端接入样例
tests/            22 个测试，跑 pytest 即可
```

## 设计取舍

本地优先。数据只有一个 sqlite 文件，备份就是拷文件，离线可用。

少依赖。核心只依赖 fastapi、uvicorn、pyyaml；解析和向量都是可选项。MCP server 用纯标准库实现，不绑官方 SDK，装不装 SDK 都能接。

不编造。组稿时如果库里没有这家酒店，会明确警告「只有同地区参考，写房型和服务前必须先补采集」；提示词里也会写死「资料里没有的一律不写」。宁可写少，不写错。

可解释。每条参考都带来源路径和入选理由（同酒店／同地区／关键词兜底），写完能逐条核对。

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
