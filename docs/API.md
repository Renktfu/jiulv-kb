# 接口文档

服务启动后打开 `http://127.0.0.1:8787/docs`，可以直接看到自动生成的 OpenAPI 文档并在线试调。这里写的是设计意图和几个容易用错的地方。

鉴权：请求头 `X-API-Key: <key>`，或 `Authorization: Bearer <key>`。`security.api_keys` 为空时不校验；配了密钥但请求来自本机，且 `allow_localhost_without_key` 为 true，也放行。

## GET /api/v1/health

不需要鉴权。返回版本、文档数、数据库路径、语义检索是否开启、已配置的来源列表。适合当健康检查。

## GET /api/v1/stats

库的规模与分布：文档数、段落数、钩子数、组稿记录数、回填数、总字数，以及 `by_type`、`by_region`、`by_source`。`facets` 里有地区和类型的计数，前端筛选项就是用这个渲染的。

## GET /api/v1/search

参数：

- `q` 必填。中文两字以上都能命中；空格分词，多个词是 AND 关系，全部不中时自动退回 OR。
- `region`／`doc_type`／`hotel`／`group`／`grade`／`source` 可选，精确匹配。
- `limit` 默认 10，上限 50。`offset` 翻页。
- `mode`：`auto`（默认）、`keyword`、`semantic`、`hybrid`。`auto` 在配好语义检索时走混合，否则走关键词。

返回 `total`、`hits`。每个 hit 里 `doc` 是文档元数据，`snippet` 是带 `«…»` 命中标记的原文片段（前端转成高亮），`duplicates` 表示还有几份同名副本被折叠掉了，`similarity` 只在语义检索时有值。

## GET /api/v1/docs

列表，支持 `limit`／`offset`／`order`（`recent`／`grade`／`title`）和与 search 相同的筛选参数。不带 `q`，用于浏览。

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

已收录来源，含路径、文档数、上次扫描时间。排查「为什么这篇没进来」先看这个。

## 错误约定

参数错误返回 422（FastAPI 默认），未授权 401，找不到资源 404。`/api/v1/generate` 的模型调用失败返回 400 并带原因。
