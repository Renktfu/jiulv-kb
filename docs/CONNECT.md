# 接上 WorkBuddy 与 OpenCLaw 的资料库

这篇讲「库怎么持续跟着两个 agent 长大」，而不是一次性导进去。

## 一句话结论

不拷贝、不导出、不做同步工具。两边的资料库就在本机磁盘上，库里只记路径，
用「目录直连 + 指纹增量 + 回写池」三段拼成一个闭环：

```
WorkBuddy 资料库 ─┐                        ┌─ 检索 / 注解 / 图谱 / 组稿 ─→ Agent
                  ├─→ 索引（SQLite）─→ ────┤
OpenCLaw 资料库  ─┘         ↑              └─→ 回写池 ←── Agent 写完的稿
                            └──── 指纹变了才重扫 ────┘
```

## 第一段：目录直连

```bash
jiulv connect --dry-run     # 先看看本机探测到什么，什么都不写
jiulv connect               # 写入 sources.yaml
```

`jiulv connect` 会按预设的「库类型」去探测，探测到就写进 `sources.yaml`。
默认探测这些位置：

| 来源 | 位置 | 说明 |
| --- | --- | --- |
| WorkBuddy 主资料库 | `~/WorkBuddy/*/小红书酒旅资料库` | 目录带批次时间戳，多个命中取**最近改动的那个** |
| WorkBuddy 产出 | `~/WorkBuddy/xhs-*` | 蓝V 专库、流水线批次、成稿目录，有几个连几个 |
| OpenCLaw 参考库 | `~/.openclaw/workspace/reference` | 瑞克克素材库、房型、政策、案例 |
| OpenCLaw 记忆 | `~/.openclaw/workspace/memory` | agent 自己沉淀的经验与复盘 |
| OpenCLaw 小红书工作区 | `~/.openclaw/workspace-xhs-*` | 写手/审稿/交付各 agent 的产出 |
| OpenCLaw 项目库 | `~/.openclaw/workspace/projects` | 夹带代码仓库，**只在 `--deep` 时连** |
| Agent 回写 | `<项目>/data/inbox` | 回写池，见第三段 |

装法不标准的人不用改代码，两个环境变量指过去就行：

```bash
export JIULV_WB_ROOT=~/mine/WorkBuddy
export JIULV_OC_ROOT=~/mine/.openclaw
```

### 为什么写的是 sources.yaml 而不是 config.yaml

`config.yaml` 是你手写的地方，程序往里写会毁掉注释和排版。所以探测结果落到
同目录的 `sources.yaml`，规则是：

- `config.yaml` 里的 `sources` **永远优先**；
- 同一路径或同名，自动来源直接丢弃，不会把同一批文件收两遍；
- 想停掉某个自动来源，不用删，给它加一行 `enabled: false`：

```yaml
sources:
  - name: OpenCLaw 项目库
    path: /Users/you/.openclaw/workspace/projects
    enabled: false
```

## 第二段：指纹增量

```bash
jiulv sync              # 该跑才跑：指纹没变就整段跳过，约 0.2 秒
jiulv watch             # 常驻，默认 120 秒看一次
```

指纹 = 每个来源的（文件数, 总字节, 最新 mtime）。三元组没变就返回
`skipped: true`，连数据库都不打开；变了才进收录流程，而收录内部还会按单文件
mtime 二次过滤，所以真实成本通常是「两千次 stat + 几篇解析」。

实测：两千篇规模的库，无变化 0.19 秒；新增 333 篇 6.7 秒（含一万五千条注解重算）。

常驻建议放后台，别占终端：

```bash
nohup jiulv watch > data/watch.log 2>&1 &
```

或者用系统定时任务，一天两次足够：

```cron
0 9,21 * * * cd /path/to/jiulv-kb && .venv/bin/jiulv sync >> data/sync.log 2>&1
```

状态落在 `data/sync_state.json`，里面记着每个来源的指纹和最近 20 次同步的
`+新增 / ~更新 / -删除`，界面上「来源」页读的就是这份。

## 第三段：回写池

前两段是「库 → agent」。这一段是反过来的，也是「迭代」真正的意思：
**agent 写出来的东西要能回到库里，下一轮写作才有新素材。**

任何接进来的 agent 写完一篇，调一次接口：

```bash
curl -X POST http://127.0.0.1:8787/api/v1/inbox \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "三亚艾迪逊带娃实测",
    "content": "低楼层看不到海，带娃要选高层双床。",
    "kind": "note",
    "hotel": "三亚艾迪逊",
    "region": "海南",
    "agent": "openclaw",
    "note": "用「楼层」这个具体细节否定默认选项"
  }'
```

走 MCP 的话是一句话：

> 写完调 kb_save 存档，note 里写清这篇为什么这么写。

落盘位置 `data/inbox/inbox_<kind>/<日期>/<标题>.md`，格式照着库里的老习惯写
（`- **标题**：` 那一套），所以元数据抽取、等级识别、互动数据解析全部复用，
不需要给回写内容开特例。写完立刻入库，立刻可被检索到，注解和图谱同步更新。

`kind` 决定它进库后的类型：`note` 笔记、`hook` 钩子、`rule` 规则、`fact` 硬资料、
`guide` 房型攻略、`ops` 代运营。

回写池是普通来源，也会被 `jiulv sync` 扫。手动删掉文件，下次同步会把它从库里
回收——不会留一篇已经不存在的稿子在里面。

## 一次典型的迭代

```
早上   jiulv sync                      # 把昨天两个 agent 的新产出收进来
写稿   kb_annotations(hotel:三亚艾迪逊) # 库里关于它的判断全摊开
       kb_context_pack(...)            # 拿规则 + 钩子 + 参考 + 事实
发布   kb_save(...)                    # 稿子和「为什么这么写」一起回写
数据   kb_feedback(likes/saves/...)    # 回填互动数据
次日   jiulv sync                      # 昨天那篇成了别人的参考
```

库不是一份静态语料，而是每写一篇就厚一点的资产。

## 排错

**探测结果是「目录不存在」**
路径和默认布局不一样。先 `jiulv connect --dry-run` 看它找的是哪儿，再用
`JIULV_WB_ROOT` / `JIULV_OC_ROOT` 指过去。

**`jiulv connect` 说「已连」但来源页没有**
说明 `config.yaml` 里已经有一条指向同路径的旧配置，用的是旧名字。这是故意的：
同名不同路径、同路径不同名都会导致同一批文件被收两遍，所以按路径去重。
想换名字，改 `config.yaml` 里那条，或删掉它再 `connect`。

**同步后篇数没变**
`jiulv sync --force` 强制跑一遍；还不变就看 `data/sync_state.json` 里各来源的
`files`，如果比目录里的少，多半是文件太小（正文不足 20 字）或格式不在
`include` 里。

**改了文件但库里还是旧的**
增量判断只看 mtime 和文件大小，不做内容 diff。某些编辑器保存时不改 mtime，
这类情况用 `jiulv ingest --rebuild` 全量重建一次——慢，但保证一致。
