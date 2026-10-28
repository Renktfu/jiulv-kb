# 接到大模型上

这个库本身不会写字。它负责把「写这篇稿需要的东西」准备好，然后交给模型写。

所以真正要解决的问题是：怎么让 Claude、Cursor、Codex、DeepSeek 这些模型知道这个库存在、并且在对的时候来取。

三条路，选一条就够了。

---

## 先选一条路

**MCP**。客户端支持 MCP 就走这条。Claude Desktop、Claude Code、Cursor、Cline、Windsurf、Cherry Studio、WorkBuddy、Codex、OpenClaw 都支持。你只要把一段 JSON 贴进配置文件，工具就出现在模型的工具列表里，调用、取结果、塞回上下文全由客户端负责。这是最省事的一条路。

**HTTP + function calling**。适合：自己写代码调模型、或者框架只认 function calling 不认 MCP、或者模型在远端（比如你在服务器上跑 DeepSeek，本机跑这个库）。你把 schema 给模型，模型吐出工具名和参数，你自己发 HTTP 请求，再把结果塞回去。

**OpenAPI 导入**。适合：Dify、Coze（扣子）、ChatGPT 的 GPTs，这类平台不支持本地 stdio，只能吃一个 OpenAPI 描述文件，然后自己生成工具。

三条路后面是同一个库、同一个数据库，能力完全一样，区别只是「谁负责跑工具」：MCP 客户端跑，你的代码跑，还是平台跑。

---

## 第 0 步：先拿到你的绝对路径

不管走哪条路，都要填几个绝对路径。手抄必错，用这条命令打出来：

```bash
cd /path/to/jiulv-kb
.venv/bin/python -m jiulv.cli where
```

它会打印：项目根目录、配置文件、数据库、Python 解释器、服务地址、服务在不在跑，以及三段可以直接复制的配置（Claude/Cursor/Cline 通用的 JSON、Codex 的 TOML、Claude Code 的一条命令）。

`where --json` 出结构化结果，方便脚本用。

**为什么必须用绝对路径。** 客户端启动 MCP 子进程时的 `PATH` 和你终端里的不一样，也不继承你已经 `source .venv/bin/activate` 的状态。配置里写 `jiulv` 或 `python`，大概率报「找不到命令」或者「No module named jiulv」。写全路径就没这问题。

**为什么推荐 `python -m jiulv.mcp_server` 而不是 `jiulv`。** 前者不依赖 PATH、不依赖 shebang、不依赖可执行权限，Windows 上也一样跑。`jiulv` 那个可执行文件在 macOS/Linux 上能用，但它是靠文件头里的解释器路径启动的，换个环境就废。

---

## 一、十一个工具，什么时候该调

模型不是靠猜的，它靠工具描述。下面这些描述已经写进了 schema，接上就能用。但你自己也要知道每个工具是干什么的，不然看不出模型哪里调错了。

### kb_context_pack

**写整篇稿之前调这个，是主出口。**

参数：`hotel`、`region`、`persona`、`pain`、`hook_type`、`goal`、`angle`、`extra_facts`。全部可选，填得越全包越准。

返回：一段拼好的 `prompt`（风格规则 + 钩子 + 同地区爆款结构 + 可核对事实 + 输出约束）、`warnings`、`references`（带入选理由）、`coverage`（各类素材命中数）。

什么时候调：每写一篇稿，第一步就调它。不要自己拼提示词。

**别拿它当搜索用。** 它不返回原文，只返回加工过的上下文。

### kb_search

**确认资料在不在。**

参数：`query` 必填，`region`、`doc_type`、`hotel`、`limit`。

返回：`mode`、`total`、`hits`。每条 hit 带 `uid`、`title`、`type`、`grade`、`region`、`hotel`、`metrics`（赞藏评）、`snippet`（带高亮标记的命中片段）、`path`（原始文件路径，可以点回去对账）。

什么时候调：写到具体楼层、房型、接机时长这种事实之前，先用它确认库里有。写完想核对某个数字，也用它。

**它是检索，不是问答。** 它不会总结，只给片段。

### kb_get_doc

**把一篇看全。**

参数：`uid_or_title`，uid 或者标题片段都行。

返回：`found` 加 `doc`。doc 里有全文 `content`，还有 `annotations`（注解，带 `kind`、`text`、`evidence`、`origin`）、`concepts`（所属概念）。

什么时候调：`kb_search` 找到的片段不够，需要看全文；或者需要这篇的注解来判断「这篇为什么值得学」。

找不到时会返回 `found: false` 和一句「库里没有这篇，先补采集，不要凭空写」。看到这句就停手，别硬写。

### kb_annotations

**写某家酒店之前，把库里关于它的判断全摊在桌上。**

参数：`focus` 必填，格式是 `类型:键`，比如 `hotel:三亚保利瑰丽`、`region:海南`、`pain:P2`。`kinds` 可以指定只取哪几类注解，`limit` 控制条数。

返回：`focus`、`docs`（这个概念下有多少篇）、`total`、`items`。每条注解带 `from`（来自哪篇）和 `uid`。

什么时候调：写一家酒店之前。它把几十篇笔记里散落的「必须保留的保留意见」「可写的卖点」「预订启示」「采集点评」一次性汇总去重。比你逐篇翻快十倍。

**这是这套库里最容易被忽略、但价值最高的一个工具。**

### kb_graph

**顺着概念找相关的东西。**

参数：`focus` 可选，`limit`。

不传 `focus`：返回概念全景，看哪些地区、酒店、痛点互相连着。传了：返回该节点的一跳邻居。

`focus` 写法：`hotel:xxx`、`region:xxx`、`pain:P2`、`d:<uid>`。

什么时候调：想知道「还有谁写过这家酒店」「这个痛点还有哪些笔记」「这家酒店和哪几家经常一起出现」。

**它不做检索。** 不传关键词，只走图上的边。

### kb_hooks

**取结构化开头钩子。**

参数：`hook_type`（反常识 / 决策瘫痪 / 代价警示 / 身份代入 / 时间窗口 / 数字具体）、`audience`（M1 M2 M3 C1 C2 F1，可以空格分隔多个）、`pain`（P1-P7）、`limit`。

返回：每条带原文、类型、人群、痛点、心理机制、期望动作。

什么时候调：写标题和第一句时。这是已经验证过能用的原句，不是模板。

### kb_style_rules

**动笔前读规则。**

参数：`topic`（比如「价格」「房型描写」「避坑」）、`limit`。

返回：`items`（规则文档的标题和要点）+ `guardrails`（组稿底线，就是那些绝对不能违反的红线）。

什么时候调：第一次接这个库时读一次，建立整体认知。之后 `kb_context_pack` 会自动带上相关规则，不用每次都调。

### kb_stats

**判断资料够不够。**

参数：无。

返回：文档数、段落数、钩子数、地区分布、类型分布、`unique_docs`（去重后的内容数）。

什么时候调：接到一个没写过的地区或酒店时，先看这个。某个地区只有 3 篇参考，你就知道这次写不出深度，只能写泛内容。

### kb_feedback

**发布之后回填。**

参数：`title`、`hotel`、`region`、`hook_type`、`audience`、`likes`、`saves`、`comments`、`views`、`is_hit`、`note`。

`hook_type` 必须填对，填错这条数据就废了，后面统计会失真。

### kb_sync

**很久没搜到新东西时调。**

参数：`force`、`source`。

指纹没变会直接返回 `skipped: true`，代价大概 0.1 秒。所以不确定的时候直接调，不用怕。

### kb_save

**写完存档。**

参数：`title`、`content` 必填，`kind`、`hotel`、`region`、`grade`、`agent`、`note`、`metrics`。

`note` 写「这篇为什么这么写」，一句话。它会进正文顶部，下一篇组稿时会出现在参考里。

**这是让库越用越厚的唯一办法。** 只取不存，半年后库里还是那批老素材。

---

## 二、一条真实的调用链

模型接上之后，写一篇文章大概是这个顺序。它自己会走，不用你指挥，但你要能看出它哪里走错了。

```
1. kb_context_pack  拿包。看到 warnings 里说没有这家酒店资料，就该停下来问人
2. kb_search        确认要写的房型、楼层、服务在库里存在
3. kb_get_doc       片段不够就取全文，顺便看注解
4. kb_annotations   写知名酒店时，取全部保留意见，避免写成软文
5. （模型按 prompt 写正文）
6. kb_save          把稿子和判断存回库
7. kb_feedback      发布后回填
```

第 1 步之后如果 `warnings` 里有「库里没有这家酒店的直接资料」，正确的行为是**停下来告诉人**，而不是硬写。写出来的东西一定会在评论区被打脸。

你可以直接这样要求它：

> 每次调 kb_context_pack 之后，先把 warnings 念给我听，有「没有直接资料」这一条就停下别写。

---

## 三、路线一：MCP

### 通用配置（JSON 类客户端）

Claude Desktop、Cursor、Cline、Windsurf、Cherry Studio、WorkBuddy、OpenClaw 用的都是这个结构，只是文件位置不同：

```json
{
  "mcpServers": {
    "jiulv-kb": {
      "command": "/绝对路径/jiulv-kb/.venv/bin/python",
      "args": ["-m", "jiulv.mcp_server"],
      "env": {
        "JIULV_CONFIG": "/绝对路径/jiulv-kb/config.yaml",
        "PYTHONPATH": "/绝对路径/jiulv-kb"
      }
    }
  }
}
```

`command` 换成 `jiulv where` 打印出来的 Python 路径。三个字段别省：`JIULV_CONFIG` 保证读到对的库，`PYTHONPATH` 保证能 import 到 `jiulv` 包。

Windows 上路径是 `D:\\jiulv-kb\\.venv\\Scripts\\python.exe`，JSON 里反斜杠要写两个。

### 通用验证：先确认 server 自己通

接不上时先别怀疑客户端，先验 server：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | /绝对路径/jiulv-kb/.venv/bin/python -m jiulv.mcp_server
```

能打出版本号和十一个工具名，说明 server 没问题，问题在客户端配置。打不出来就是 server 侧的问题，先修这一步。

`jiulv where` 会把这条命令按你的实际路径拼好打印出来。

### Claude Desktop

配置文件：

macOS 是 `~/Library/Application Support/Claude/claude_desktop_config.json`。
Windows 是 `%APPDATA%\Claude\claude_desktop_config.json`。

把上面那段 `mcpServers` 合并进去。文件原来有别的服务器就合并，别整个覆盖。改完**完全退出 Claude 再打开**，光关窗口不行，要退出进程。

打开后输入框右下角会出现工具图标，点开能看到 `jiulv-kb` 和十一个工具。

### Claude Code

一条命令：

```bash
claude mcp add jiulv \
  -e JIULV_CONFIG=/绝对路径/config.yaml \
  -e PYTHONPATH=/绝对路径 \
  -- /绝对路径/.venv/bin/python -m jiulv.mcp_server
```

`jiulv where` 会把这条按你的路径拼好。

也可以写在项目根的 `.mcp.json` 里，这样换机器跟着仓库走：

```json
{
  "mcpServers": {
    "jiulv-kb": {
      "command": "/绝对路径/.venv/bin/python",
      "args": ["-m", "jiulv.mcp_server"],
      "env": {"JIULV_CONFIG": "/绝对路径/config.yaml", "PYTHONPATH": "/绝对路径"}
    }
  }
}
```

验证：`claude mcp list` 能看到 jiulv，或者在对话里说「用 kb_stats 看看库里有多少篇」。

### Cursor

全局配置在 `~/.cursor/mcp.json`。只想给某个项目用，就放在项目根的 `.cursor/mcp.json`。

内容就是上面那段 `mcpServers` JSON。

改完在 Cursor 设置里找到 MCP，看到 jiulv-kb 是绿灯、工具数 11 就成。首次会要你确认，点允许。

### Cline / Roo Code

这两个是 VS Code 扩展，配置方式都是：

打开侧栏的 MCP Servers 面板，点 Configure MCP Servers，会打开一个 `cline_mcp_settings.json`，把 `mcpServers` 那段贴进去保存。

面板里会列出 `jiulv-kb`，展开能看到工具，每个工具后面有个开关，默认开。

### Windsurf

配置文件在 `~/.codeium/windsurf/mcp_config.json`。格式同上。

Windsurf 的设置里也有 MCP 面板，可以点开直接编辑，效果一样。

### Cherry Studio

设置 → MCP 服务器 → 添加服务器。类型选 `stdio`，然后：

命令填 Python 的绝对路径。
参数填 `-m` 和 `jiulv.mcp_server` 两项。
环境变量填 `JIULV_CONFIG` 和 `PYTHONPATH`。

保存后点一下测试，能看到工具列表就成了。

Cherry Studio 的好处是它可以同时挂多个模型，同一个库给不同模型用，方便对比。

### WorkBuddy

配置文件是 `~/.workbuddy/mcp.json`。样例见 `examples/workbuddy/mcp.json`。

合并进去之后，**必须**到连接器管理页右上角的「自定义连接器」里点一下「信任」，否则不生效。这一步最容易被漏掉，漏了会表现为「配置明明写了但工具不出现」。

### Codex

配置文件是 `~/.codex/config.toml`，追加一段：

```toml
[mcp_servers.jiulv]
command = '/绝对路径/.venv/bin/python'
args = ["-m", "jiulv.mcp_server"]
startup_timeout_sec = 20

[mcp_servers.jiulv.env]
JIULV_CONFIG = '/绝对路径/config.yaml'
PYTHONPATH = '/绝对路径'
```

注意 TOML 里 `command` 用的是单引号。单引号是字面量字符串，Windows 路径里的反斜杠不用转义；换成双引号就必须写成 `\\`。

`startup_timeout_sec` 给 20 秒。首次冷启动要打开 SQLite、载入词表，机器慢的时候 10 秒不够。

也可以用命令加：`codex mcp add jiulv -- /绝对路径/.venv/bin/python -m jiulv.mcp_server`，再用 `codex mcp list` 确认。

### OpenClaw

OpenClaw 读标准 `mcpServers` 结构。按你的版本放到 workspace 下的 `mcp.json`，或者合并进全局配置。样例见 `examples/openclaw/mcp.json`。

如果 OpenClaw 里既有它自己的资料库工具，又有这套库，建议分工：它的库负责它自己沉淀的语料，这套库负责小红书爆款和写作规则，让 Agent 按任务类型选。两边重复调会把上下文弄脏。

---

## 四、路线二：HTTP + function calling

适合自己写代码、或者框架只认 function calling 的情况。

### 取 schema

```bash
jiulv tools --format openai > tools.json
```

或者服务跑着的时候直接取：

```bash
curl http://127.0.0.1:8787/api/v1/tools
```

两者内容一样，都是标准的 `{"type":"function","function":{...}}` 数组，塞进请求的 `tools` 字段即可。加 `?format=mcp` 拿到原始的 `inputSchema`。

### 执行：用工具网关，别自己映射

关键点在这里。**不要把工具名一个个映射成 REST 端点。**

正确做法是调这个端点：

```
POST /api/v1/tool/{工具名}
请求体就是工具的参数对象
```

比如：

```bash
curl -X POST http://127.0.0.1:8787/api/v1/tool/kb_search \
  -H 'Content-Type: application/json' \
  -d '{"query":"三亚 亲子 选房","limit":3}'
```

返回的结构和 MCP 里拿到的**完全一样**，一个字都不差。这意味着 schema、参数、返回值三样都对齐，你不需要为两条路线写两套代码。

为什么不建议自己映射：`kb_style_rules` 在 MCP 里除了规则文档还附带组稿底线 `guardrails`，映射到 `/api/v1/rules` 就拿不到；`kb_annotations` 是「取概念下文档 + 逐篇取注解 + 去重排序」的组合，REST 里没有单个端点干这件事。这种不一致迟早会咬人。

### 一段能跑的代码

```python
import json
import urllib.request

BASE = "http://127.0.0.1:8787"

def call(name, args):
    req = urllib.request.Request(
        f"{BASE}/api/v1/tool/{name}",
        data=json.dumps(args).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

tools = json.loads(urllib.request.urlopen(f"{BASE}/api/v1/tools").read())
pack = call("kb_context_pack", {"hotel": "三亚仁恒皇冠", "region": "海南", "persona": "M2"})
print(pack["prompt"])
```

完整一点的版本在 `examples/http/client.py`，带工具循环。

### 各家模型都一样写

function calling 的协议是通的，只是字段名有小差别。下面这些写法本质相同：

OpenAI、DeepSeek、Kimi、豆包、通义、智谱，都用 `tools` + `tool_calls`。传 `tools.json` 里的数组，模型返回 `tool_calls`，你逐个执行，把结果用 `role: "tool"` 的消息塞回去，再请求一轮。循环到模型不再要工具为止。

Anthropic 的 Messages API 字段名不同：工具放 `tools`，但结构是 `{"name","description","input_schema"}`，返回的是 `tool_use` 内容块，结果要塞成 `tool_result`。需要把我们的 schema 转一下：`function.parameters` 改成 `input_schema`，`function.name` 提到外层。三十行代码的事。

Gemini 又是另一套 `functionDeclarations`。同样只是字段名差异。

### 循环里要注意三件事

第一，**工具结果别截断**。`kb_context_pack` 的 `prompt` 可能三四千字，截了模型就写不全。它的价值就在完整约束。

第二，**限制轮数**。给个上限比如 8 轮，防止模型在 `kb_search` 和 `kb_get_doc` 之间反复横跳。

第三，**把 `warnings` 透给模型**。很多人只把 `prompt` 塞回去，把 `warnings` 丢了。那条「没有这家酒店的直接资料」恰恰是最该让模型看到的。

### 只挂需要的工具

十一个工具的 schema 大概占两千到三千个 token。如果你的场景只有「写稿」一件事，只挂 `kb_context_pack`、`kb_search`、`kb_get_doc`、`kb_save` 四个就够了，省下来的上下文留给正文。

反过来，如果做的是「库的运维助手」，就只挂 `kb_stats`、`kb_sync`、`kb_sources` 这类。

---

## 五、路线三：OpenAPI 导入

Dify、Coze、ChatGPT 的 GPTs 这类平台不吃本地 stdio，只能吃一个 OpenAPI 描述文件。

我准备了一份精简版：`examples/openapi/jiulv-mini.yaml`。只含四个最常用的操作（检索、取全文、组稿、回写），参数都展开成了具名参数，导入之后表单是填好的，不用手填。

### 本机地址的问题

这份文件里的 `servers` 是 `http://127.0.0.1:8787`。**这个地址只有你自己电脑能访问**。平台在云端，它访问你的 127.0.0.1 就是访问它自己。

三种解法，按省事程度排：

**一，换成本机跑。** Dify 可以本地 Docker 部署，Coze 有开源版，ChatGPT 不行。本地部署时把 `servers` 改成 `http://host.docker.internal:8787`，并把服务用 `serve --host 0.0.0.0` 起来。

**二，打隧道。** `cloudflared tunnel --url http://127.0.0.1:8787` 或者 `ngrok http 8787`，会给你一个公网 HTTPS 地址，填进 `servers`。

**这一条有个前提：必须配 API Key。** 打隧道等于把库开到公网，`security.api_keys` 留空就是谁拿到地址谁都能读你的客户资料。配置方法：

```yaml
security:
  api_keys: ["换成一串足够长的随机字符"]
  allow_localhost_without_key: false
```

然后导入的 OpenAPI 里要加认证声明，或者在平台的安全设置里填请求头 `X-API-Key`。

**三，不用 OpenAPI，改用 function calling。** 如果你的平台支持自定义代码节点（Dify 有代码节点，Coze 有），直接在节点里发 HTTP 请求调 `/api/v1/tool/{name}`，比导入 OpenAPI 更自由。

### Dify

工具 → 自定义 → 创建自定义工具 → 导入 OpenAPI schema，选 `jiulv-mini.yaml`。

导入后 Dify 会列出四个操作。逐个测试一下，能返回数据就成。然后在工作流里当工具节点用，或者挂到 Agent 上。

Dify 的 Agent 模式也支持 MCP（较新版本），如果你的版本有 MCP 支持，优先用 MCP + 隧道，比 OpenAPI 稳。

### Coze / 扣子

插件 → 创建插件 → 导入 OpenAPI → 上传 yaml。

注意 Coze 要求 `operationId` 唯一且不含特殊字符，这份文件里的四个都符合。

Coze 的模型（豆包等）对中文工具描述理解得不错，导入后不用改描述。

### ChatGPT 的 GPTs Actions

GPTs → Configure → Actions → 导入 schema。ChatGPT 只吃 HTTPS，所以必须走隧道。

鉴权在 Actions 的认证设置里配，类型选 API Key，位置选 Header，名字填 `X-API-Key`。

**注意数据边界。** 走公网之后，你的客户酒店名、笔记原文会经过第三方隧道服务。如果资料敏感，这条路不要走，改用本地客户端。

---

## 六、给模型一段系统提示词

接上之后，模型知道有这些工具，但不一定知道该怎么用。加一段系统提示词会稳很多。下面是可直接复制的版本：

```
你有一套酒旅文案知识库，通过 kb_* 工具访问。

写任何酒店笔记之前，先调 kb_context_pack 拿上下文包，按它返回的 prompt 写正文。
prompt 里已经包含风格规则、钩子、参考结构和输出约束，不要再自己另起一段描述需求。

拿到包之后，先把 warnings 念给我听。如果出现「库里没有这家酒店的直接资料」，
停下来问我，不要硬写。

写到具体楼层、房型、朝向、接机时长这类事实时，先用 kb_search 或 kb_get_doc 确认。
库里有的才写，库里没有的一律不写。宁可写少，不写错。

写知名酒店时，调一次 kb_annotations 取全部保留意见，写进稿子里。这类意见不写，
稿子就成了软文。

写完调 kb_save 存档，note 一栏写清这篇为什么这么写。
发布后调 kb_feedback 回填数据，hook_type 要填对。

全程不要编造库外的事实，也不要为了流畅把「不确定」写成「确定」。
```

这段的作用是把「什么时候该停下」写进指令里。模型天然倾向于把任务做完，不给它一条明确的停手线，它会用想象力补齐缺失的资料。

---

## 七、排错

**工具不出现。**
先跑第 0 步那条 printf 验证，确认 server 通。server 通但客户端不出现：九成是路径错（用了相对路径、用了 `~`、`~` 在 JSON 里不会展开）、或者 `PYTHONPATH` 漏了、或者 WorkBuddy 忘了点信任。改完配置要重启客户端，Claude Desktop 必须完全退出进程。

**工具出现了，但调用报错。**
单独跑一次 `jiulv --config /绝对路径/config.yaml stats`。能跑说明库没问题，是客户端环境问题；不能跑看报什么错。最常见的是 `JIULV_CONFIG` 指到了一个不存在的路径，此时会静默用默认配置，表现为「库是空的」。

**中文乱码，或者检索词不生效。**
Windows 上常见。MCP server 内部已经按字节读写了，如果你是通过自己写的 HTTP 客户端调，注意请求体必须是 UTF-8 编码，`Content-Type` 要带 `charset=utf-8`。

**模型不调工具，直接开始写。**
两种原因。一是工具描述被客户端的设置截断了（有些客户端会精简 description），二是系统提示词没说清必须在写之前调。先加第六节那段提示词。

**调了 kb_context_pack，但模型没按 prompt 写。**
检查你是不是在 prompt 之外又补了自己的要求。重复信息会稀释约束。另外确认你把 `prompt` 原样传给了模型，而不是只传了 `references`。

**检索一直是空结果。**
`kb_stats` 看 `docs` 是不是 0。是 0 说明没收录，回去跑 `jiulv sync`。不是 0 就换关键词，或者 `kb_search` 用 `region` 而不是把地区写进 `query`。

**想让模型写得更长/更短/更活泼。**
不要改提示词去加要求，改 `kb_context_pack` 的 `angle` 参数，或者改库里的风格规则文档。规则改了，所有稿子都受益；提示词改了，只有这一次受益。

---

## 附：三条路线怎么选，一句话

本机有客户端，走 MCP。本机没客户端，或者要写代码，走 HTTP + function calling。平台在云端，先想办法把服务暴露出去，再用 OpenAPI 或 MCP。

不管哪条路，第 0 步的 `jiulv where` 都要先跑一遍。路径对了，剩下的都是复制粘贴。
