# 接口文档

服务启动后打开 `http://127.0.0.1:8787/docs`，可以直接看到自动生成的 OpenAPI 文档并在线试调。这里写的是设计意图和几个容易用错的地方。

鉴权：请求头 `X-API-Key: <key>`，或 `Authorization: Bearer <key>`。`security.api_keys` 为空时不校验；配了密钥但请求来自本机，且 `allow_localhost_without_key` 为 true，也放行。

## GET /api/v1/health

不需要鉴权。返回版本、文档数、数据库路径、语义检索是否开启、已配置的来源列表。适合当健康检查。

## GET /api/v1/stats

库的规模与分布：文档数、段落数、钩子数、组稿记录数、回填数、总字数，以及 `by_type`、`by_region`、`by_source`。`facets` 里有地区和类型的计数，前端筛选项就是用这个渲染的，计数按去重口径算。`docs` 是收录条数，`unique_docs` 是去掉各来源副本后的内容数，两个数会差得比较多，别混用。

## GET /api/v1/search

参数：

- `q` 必填。中文两字以上都能命中；空格分词，多个词是 AND 关系，全部不中时自动退回 OR。
- `region`／`doc_type`／`hotel`／`group`／`grade`／`source` 可选，精确匹配。
- `limit` 默认 10，上限 50。`offset` 翻页。
- `mode`：`auto`（默认）、`keyword`、`semantic`、`hybrid`。`auto` 在配好语义检索时走混合，否则走关键词。

返回 `total`、`hits`。每个 hit 里 `doc` 是文档元数据，`snippet` 是带 `«…»` 命中标记的原文片段（前端转成高亮），`duplicates` 表示还有几份同名副本被折叠掉了，`similarity` 只在语义检索时有值。

## GET /api/v1/docs

列表，支持 `limit`／`offset`／`order`（`recent`／`grade`／`title`）和与 search 相同的筛选参数。不带 `q`，用于浏览。

`dedupe` 默认 `true`：同一篇内容常被几个来源库各收一份（实测 1888 条收录里只有 830 篇是不同内容），不折叠的话点一个地区满屏都是副本。分组按「洗净标题+类型」，标题太短才退回内容哈希；每组挑等级最高、正文最长的那份当代表，其余份数放在 `duplicates` 上。`total` 是去重后的数，所以它和 `facets` 里各标签的计数能对得上。要拿原始逐条列表传 `dedupe=false`。

## GET /api/v1/docs/{uid}

取一篇全文，含 `content`，以及四组关联数据：

- `annotations` 注解，按权重降序。每条带 `kind`（takeaway／con／booking／pro／voice／review／signal／title 等）、`text`、`evidence`（原文引用）、`origin`（`source` 原文摘录 / `derived` 规则推算）。
- `concepts` 所属概念，八类：region／hotel／author／series／audience／pain／hook／technique。
- `backlinks` 反向链接：通过共享概念找上来的其他笔记，带 `shared`（共享概念数）和 `via`（经由哪些概念）。
- `related` 同酒店优先，其次同地区同类型。

`uid` 也接受标题片段：传不进 uid 时会退回 `title LIKE` 匹配。

## GET /api/v1/graph

图谱。不传 `focus` 返回概念全景图（节点是概念，边是两个概念共同出现过的笔记数）；传了返回该节点的一跳子图。

`focus` 支持三种写法：`hotel:三亚保利瑰丽`（类型:键）、`c:12`（概念 id）、`d:88`（文档 id）。

返回 `{focus, nodes, edges}`。节点带 `kind`、`label`、`size`（度数）；文档节点额外带 `uid`，点击可直接打开原文。

## GET /api/v1/concepts

概念列表。不带 `kind` 返回各类型的数量；带 `kind` 返回该类型下按文档数排序的概念。`kind` 取 region／hotel／author／series／audience／pain／hook／technique。

## GET /api/v1/concept/docs

某个概念下的全部笔记，参数 `ref`（如 `pain:P2`、`hotel:三亚保利瑰丽`）、`limit`、`offset`。

## GET /api/v1/docs/{uid}/raw

返回 `text/markdown` 原文，方便直接喂给不需要 JSON 包装的地方。

## GET /api/v1/hooks

结构化钩子。参数 `hook_type`、`audience`、`pain` 都支持模糊匹配，`audience` 和 `pain` 可用空格分隔多个码（如 `M1 M3`），`bucket` 取 `opening`／`save`／`comment`／`fail`／`topic`。已验证的条目排在前面。

## GET /api/v1/rules

写作规则类文档的标题、摘要、字数、路径。适合让 Agent 先建立一个「有哪些规则」的认知。

## GET /api/v1/taxonomy

人群、痛点、钩子类型、组稿底线的定义。默认读包内 `jiulv/taxonomy.yaml`，可以在 config 里用 `taxonomy: 路径` 换成自己的。

## POST /api/v1/compose

组稿。这是核心接口。

请求体：

```json
{
  "hotel": "三亚仁恒皇冠",
  "region": "海南",
  "persona": "M2",
  "pain": "P2",
  "hook_type": "反常识",
  "goal": "save",
  "platform": "小红书",
  "angle": "写给第一次带娃去三亚的人",
  "extra_facts": ["大堂 2025 年翻新过"],
  "max_references": 6
}
```

`persona` 和 `pain` 既接受 `M2`／`P2` 这样的码，也接受「带娃」「选房」这类中文，服务端会按 taxonomy 的别名表解析。都不填也能出包，只是参考会偏泛。

返回：

- `prompt` 拼好的完整提示词，直接可用
- `brief` 归一化后的任务参数，含解析出的 `audience_label`、`pain_label`
- `warnings` 资料不足时的提醒，务必读，不要忽略
- `references` 参考篇目，每条带 `reason`（同酒店／同地区／关键词兜底）和 `snippet`
- `hooks`、`style_rules`、`facts`、`guardrails`
- `coverage` 各类素材命中数量
- `compose_id` 这次组稿的 id，回填时用它对上

`warnings` 里最需要在意的一条是「库里没有这家酒店的直接资料」。出现它说明这次只有同地区参考，写具体房型和服务会失控。

## POST /api/v1/generate

可选。服务端直接调对话模型出稿，需要 `generation.enabled: true` 和对应的 API Key。多数情况不需要它：让 Agent 拿 `prompt` 自己写，质量更好，也省一次调用。

## POST /api/v1/feedback

回填发布数据：`title`、`hotel`、`region`、`hook_type`、`audience`、`views`、`likes`、`saves`、`comments`、`follows`、`is_hit`、`note`。`is_hit` 是你们自己定的爆款线，传布尔值。

## GET /api/v1/outcomes

最近的回填记录，外加按钩子类型聚合的 `by_hook_type`（次数、命中数、总赞、总藏）。复盘时看这张。

## GET /api/v1/sources

已连接的来源：`name`、`group`（workbuddy／openclaw／inbox）、`path`、`exists`、`doc_count`、`last_scan`。
顶层还有 `last_sync` 和 `total_docs`。排查「为什么这篇没进来」先看这个。

和 `/api/v1/connectors` 的区别：这里只列**配置里连着的**，那边连没连的都列，用来发现「本机还有个库没接」。

## GET /api/v1/connectors

探测本机所有认得出来的资料库：`profile`、`title`、`path`、`exists`、`docs`（磁盘上的可收录篇数）、`connected`、`last_scan`。
顶层 `connected` / `exists` / `docs_on_disk` / `last_sync`。只读，不写文件。

## POST /api/v1/connect

把探测到、但还没连的库写进 `sources.yaml`。query 参数 `deep=true` 连历史项目库，
`only=wb-library,oc-reference` 限定类型，`sync=true` 连完立刻同步一次。写完不需要重启服务。

## POST /api/v1/sync

增量同步。请求体 `{"force": false, "source": null}`。

指纹（每个来源的文件数、总字节、最新 mtime）没变时返回
`{"ok": true, "skipped": true, "reason": "指纹没变，跳过"}`，不开数据库，约 0.1 秒。
有变化时跑收录并返回 `report`（`added`／`updated`／`removed`／`annotations`／`duration_s`）
和 `changed`（哪几个来源变了）。

## POST /api/v1/inbox

回写一篇稿子进库，落盘到 `data/inbox/inbox_<kind>/<日期>/`，走同一条解析与注解流水线，
返回后立刻可被检索到。

```json
{
  "title": "三亚艾迪逊带娃实测",
  "content": "低楼层看不到海，带娃要选高层双床。",
  "kind": "note",
  "hotel": "三亚艾迪逊",
  "region": "海南",
  "grade": "B",
  "agent": "openclaw",
  "note": "用「楼层」这个具体细节否定默认选项",
  "metrics": {"likes": 0, "saves": 0, "comments": 0}
}
```

`kind` 取 `note`／`hook`／`rule`／`fact`／`guide`／`ops`／`daily`／`index`，决定它进库后的
`doc_type`。`note` 字段是「这篇为什么这么写」，会写进正文顶部，下一篇组稿时会出现在参考里。
`content` 为空返回 400。

## GET /api/v1/inbox

回写池最近的条目：`title`、`kind`、`path`、`mtime`、`bytes`。参数 `limit` 默认 20。

## GET /api/v1/tools

工具 schema，和 `jiulv tools` 命令同一份数据，只是走 HTTP，方便远端脚本现取现用。

`format=openai`（默认）返回 function calling 用的 `{"type":"function","function":{...}}` 数组；
`format=mcp` 返回原始的 `inputSchema`。十一个工具的定义都在这里。

## POST /api/v1/tool/{name}

按 MCP 的工具名直接执行，请求体就是该工具的参数对象，返回结构与 MCP 里拿到的**完全一致**。

```bash
curl -X POST http://127.0.0.1:8787/api/v1/tool/kb_search \
  -H 'Content-Type: application/json' \
  -d '{"query":"三亚 亲子 选房","limit":3}'
```

这是给只认 function calling、不接 MCP 的框架用的：schema 从 `/api/v1/tools` 取，
模型给出工具名和 `arguments` 之后原样 POST 过来就行。

刻意不做「工具名 → REST 端点」的映射，原因是那层映射两边的返回结构对不上：
`kb_style_rules` 在 MCP 里除了规则文档还附带组稿底线 `guardrails`，映射到
`/api/v1/rules` 就拿不到；`kb_annotations` 是「取概念下文档 + 逐篇取注解 + 去重排序」
的组合，REST 里没有单个端点干这件事。

未知工具返回 404。工具内部执行失败返回 200 带 `{"ok": false, "error": "..."}`，
方便 Agent 循环继续跑而不是直接崩。

## 错误约定

参数错误返回 422（FastAPI 默认），未授权 401，找不到资源 404。`/api/v1/generate` 的模型调用失败返回 400 并带原因。
