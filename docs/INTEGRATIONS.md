# 接四端（速查）

**完整的接入指南在 [`LLM.md`](LLM.md)。** 那份文档讲了三条路线（MCP、HTTP + function calling、OpenAPI 导入）、十一个工具分别在什么时候调、可直接复制的系统提示词、以及各类排错。第一次接先读那份。

这里只留四端最快的那一段配置，方便熟手复制。

## 先拿到绝对路径

```bash
.venv/bin/python -m jiulv.cli where
```

它会把下面每段配置按你的实际路径拼好打印出来，直接复制，不用手抄。

## 通用验证

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | .venv/bin/python -m jiulv.mcp_server
```

能打出版本号和十一个工具名，就说明 server 本身没问题。接不上时先跑这一句，把问题定位在 server 侧还是客户端侧。

## WorkBuddy

`~/.workbuddy/mcp.json`，样例见 `examples/workbuddy/mcp.json`。

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

已经存在的 `mcpServers` 里合并这一段，别整个覆盖。改完到连接器管理页右上角的「自定义连接器」里点信任，否则不生效。

## OpenClaw

样例见 `examples/openclaw/mcp.json`。格式同上，按你的版本放到 workspace 下的 `mcp.json`，或合并进全局配置。

如果 OpenClaw 里既有自己的资料库工具又有这套库，建议分工：它的库负责自己沉淀的语料，这套库负责小红书爆款与写作规则，让 Agent 按任务类型选。重复调用会让上下文变脏。

## Codex

`~/.codex/config.toml` 追加，样例见 `examples/codex/config.toml`。

```toml
[mcp_servers.jiulv]
command = '/绝对路径/.venv/bin/python'
args = ["-m", "jiulv.mcp_server"]
startup_timeout_sec = 20

[mcp_servers.jiulv.env]
JIULV_CONFIG = '/绝对路径/config.yaml'
PYTHONPATH = '/绝对路径'
```

`command` 用单引号：TOML 的单引号是字面量字符串，Windows 路径里的反斜杠不用转义。`startup_timeout_sec` 给 20 秒，首次冷启动要打开 SQLite、载入词表。

## DeepSeek Harness 与其他只认 function calling 的框架

```bash
jiulv tools --format openai > tools.json
```

文件是标准的 `{"type":"function","function":{...}}` 数组，塞进请求的 `tools` 字段即可。

工具真正执行时打到：

```
POST /api/v1/tool/{工具名}
请求体就是工具的参数对象
```

返回结构和 MCP 里拿到的完全一样，所以不需要维护一套「工具名 → REST 端点」的映射表。完整例子见 `examples/function-calling/agent_loop.py`，四十行跑通整个循环。

不想跑 MCP 也不想写代码的，`examples/http/client.py` 是直接调 REST 的写法，适合快速验证。

## 接上之后怎么用才对

四件事，按顺序做效果最好。

写单篇。调 `kb_context_pack`，让它把规则、钩子、参考、事实一次拼好，然后按 `prompt` 写。不要自己另起一段描述需求，重复的信息会稀释约束。

查事实。写到具体房型、楼层、朝向、接机时长时，先用 `kb_search` 或 `kb_get_doc` 确认。库里有就写，没有就别写，这是最重要的一条纪律。

发完回填。`kb_feedback` 记一笔，钩子类型要填对。积累几十条之后，`/api/v1/outcomes` 里的 `by_hook_type` 会告诉你哪类钩子在你账号上真的有效。

写完存档。`kb_save` 把稿子和判断一起写回库。只取不存，半年后库里还是那批老素材。

## 排错

连不上：先跑上面那句通用验证。server 通、客户端不通，八成是路径写错，或者 `PATH` 里没有虚拟环境。

工具列出来了但调用报错：`jiulv --config ... stats` 单独跑一下，确认数据库存在。数据库路径以 config.yaml 所在目录为基准。

检索结果为空但文件确实在：`jiulv --config ... sources` 看文档数，路径没扫到就是 `include`／`exclude` 写错了。`jiulv ingest -v` 会逐条打印收录情况。

更多情况见 `LLM.md` 第七节。
