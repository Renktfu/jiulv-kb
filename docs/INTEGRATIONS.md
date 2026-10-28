# 接四端

一套 MCP server，四种接法。先把路径确认好：

```bash
which jiulv                        # 通常是 .venv/bin/jiulv
realpath config.yaml               # 配置文件的绝对路径
```

配置文件里的路径一律建议用绝对路径。Agent 启动子进程时的 `PATH` 和你终端里的不一样，写 `jiulv` 常常找不到。

## 通用：验证 MCP 通不通

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jiulv --config /绝对路径/config.yaml mcp
```

能看到 `serverInfo` 和十一个工具名，就说明 server 本身没问题。接不上时先跑这一句，把问题定位在 server 侧还是客户端侧。

## WorkBuddy

配置文件：`~/.workbuddy/mcp.json`。样例见 `examples/workbuddy/mcp.json`。

```json
{
  "mcpServers": {
    "jiulv-kb": {
      "command": "/绝对路径/.venv/bin/jiulv",
      "args": ["--config", "/绝对路径/config.yaml", "mcp"]
    }
  }
}
```

已经存在的 `mcpServers` 里合并这一段，别整个覆盖。改完到连接器管理页右上角的「自定义连接器」里点信任，否则不会生效。

用起来：

> 用 kb_context_pack 给三亚瑞吉写一篇给情侣的笔记，钩子用代价警示，写完把提示词里的约束逐条自查一遍。

## OpenClaw

样例见 `examples/openclaw/mcp.json`。OpenClaw 读的是标准 `mcpServers` 结构，按你的版本放到 workspace 下的 `mcp.json`，或合并进全局配置。

```json
{
  "mcpServers": {
    "jiulv-kb": {
      "command": "/绝对路径/.venv/bin/python",
      "args": ["-m", "jiulv.mcp_server"],
      "env": {
        "JIULV_CONFIG": "/绝对路径/config.yaml",
        "PYTHONPATH": "/绝对路径"
      }
    }
  }
}
```

这里用 `python -m` 而不是 `jiulv`，是为了绕开 OpenClaw 子进程 `PATH` 里没有虚拟环境的问题。`PYTHONPATH` 指向项目根目录，保证能 import 到 `jiulv` 包。

如果 OpenClaw 里既有自己的资料库工具又有这套库，建议分工：OpenClaw 的库负责它自己沉淀的语料，这套库负责小红书爆款与写作规则，让 Agent 按任务类型选。重复调用会让上下文变脏。

## Codex

配置文件：`~/.codex/config.toml` 追加一段，样例见 `examples/codex/config.toml`。

```toml
[mcp_servers.jiulv]
command = "/绝对路径/.venv/bin/jiulv"
args = ["--config", "/绝对路径/config.yaml", "mcp"]
startup_timeout_sec = 20

[mcp_servers.jiulv.env]
JIULV_CONFIG = "/绝对路径/config.yaml"
```

Codex 启动时会握手，`startup_timeout_sec` 给到 20 秒比较稳（首次冷启动要载入 SQLite）。

## DeepSeek Harness

如果框架只认 function calling，不给 MCP，就直接用导出的 schema：

```bash
jiulv tools --format openai > tools.json
```

文件里是标准的 `{"type":"function","function":{...}}` 数组，塞进请求的 `tools` 字段即可。工具真正执行时打到本地 REST：

- `kb_search` → `GET /api/v1/search?q=...&region=...&doc_type=...&limit=...`
- `kb_get_doc` → `GET /api/v1/docs/{uid_or_title}`，返回里已经带 `annotations`、`concepts`、`backlinks`
- `kb_annotations` → `GET /api/v1/concept/docs?ref=...` 拿到文档列表后逐个取 `annotations` 汇总去重
- `kb_graph` → `GET /api/v1/graph?focus=...`
- `kb_hooks` → `GET /api/v1/hooks?hook_type=...&audience=...&pain=...`
- `kb_style_rules` → `GET /api/v1/rules`，需要主题时改用 `GET /api/v1/search?doc_type=rule&q=主题`
- `kb_context_pack` → `POST /api/v1/compose`
- `kb_stats` → `GET /api/v1/stats`
- `kb_feedback` → `POST /api/v1/feedback`
- `kb_sync` → `POST /api/v1/sync`
- `kb_save` → `POST /api/v1/inbox`

`examples/http/client.py` 是完整的调用示例，二十来行，照着改就行。

## 两个新工具的用法

`kb_annotations` 是给「写之前先弄清这家酒店」用的。传 `hotel:三亚保利瑰丽`，它会把库里所有提过这家酒店的笔记的注解汇总去重，返回「必须写的保留意见」「可写的卖点」「预订启示」「采集点评」。等于把你几十篇笔记里散落的判断一次摊在桌上，不用逐篇翻。

`kb_graph` 是给「还有哪些相关的」用的。不传 `focus` 看概念全景，传了取一跳邻居。它不做检索，别拿它当搜索用。

`kb_get_doc` 现在返回的不只是原文，还带这篇的注解和所属概念。Agent 读到一篇就能知道「这篇为什么值得学」，不用额外调一次。

`kb_sync` 是「补素材」用的。库里搜不到想要的东西时先别急着说没有，调一次 sync，
把两个资料库今天新增的收进来再搜。指纹没变会直接返回 `skipped: true`，代价很低。

`kb_save` 是反方向的，写完调一次。它会把你写的正文连同 `note`（这篇为什么这么写）
落成一篇正常素材，走同一条解析与注解流水线。下一篇写同一家酒店时，这篇已经在参考里了。
这两个工具凑起来才是闭环：素材进得来，产出回得去。

## 接上之后怎么用才对

三个用法，按顺序用效果最好。

写单篇。直接调 `kb_context_pack`，让它把规则、钩子、参考、事实一次拼好，然后按 `prompt` 写。不要自己另起一段描述需求，重复的信息会稀释约束。

查事实。写到具体房型、楼层、朝向、接机时长时，先用 `kb_search` 或 `kb_get_doc` 确认。库里有就写，没有就别写，这是这套东西最重要的使用纪律。

发完回填。`kb_feedback` 记一笔，钩子类型要填对。积累几十条之后，`/api/v1/outcomes` 里的 `by_hook_type` 会告诉你哪类钩子在你的账号上真的有效。

写完存档。`kb_save` 把稿子和判断一起回写进库，这是让库越用越厚的唯一办法。只取不存，
半年后库里还是那批老素材。

## 排错

连不上：先跑上面那句通用验证。server 通、客户端不通，八成是路径写错或 `PATH` 问题。

工具列出来了但调用报错：`jiulv --config ... stats` 单独跑一下，确认数据库存在。数据库路径以 config.yaml 所在目录为基准。

检索结果为空但文件确实在：`jiulv --config ... sources` 看文档数，路径没扫到就是 `include`／`exclude` 写错了。`jiulv ingest -v` 会逐条打印收录情况。
