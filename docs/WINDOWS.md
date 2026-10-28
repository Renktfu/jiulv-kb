# Windows 交接文档

给接手这个仓库的 AI 或人看。目标是：**在 Windows 上把 jiulv-kb 跑起来，
并且知道改哪里、改坏了什么症状。**

这份文档只讲 Windows。通用架构见 `ARCHITECTURE.md`，接口见 `API.md`，
资料库怎么连见 `CONNECT.md`。

---

## 一、先说清楚：这是程序，不是网站

很多人第一次看到 `http://127.0.0.1:8787` 会以为它是个网页项目。不是。

它是一套 Python 程序，有三种运行形态，共用同一个 SQLite 文件：

一是命令行。`jiulv init / connect / sync / watch / ingest / search / compose / stats / serve / mcp / doctor`，
一次性执行，跑完就退出。

二是本地 HTTP 服务。`jiulv serve` 在本机 8787 端口起一个 FastAPI 进程，
对外提供 `/api/v1/*` 和自动生成的 `/docs`（OpenAPI）。

三是 MCP server。`jiulv mcp` 用 stdio 走 JSON-RPC，给 OpenClaw / Codex /
WorkBuddy / DeepSeek Harness 这类 agent 直接调库。

所谓「网页界面」只是第二种形态顺带托管的一组静态文件（`jiulv/web/`：一个
`index.html`、一个 `app.js`、一个 `style.css`，没有构建步骤，没有 npm）。
它必须由 `jiulv serve` 起着才能打开，关掉进程页面就白屏。

所以：**没有服务器要部署，没有数据库要装，没有前端要打包。**
全部状态就是 `data/kb.sqlite3` 一个文件（外加 `data/sync_state.json` 一个同步指纹）。

---

## 二、Windows 上从零跑起来

PowerShell，逐段执行。假设仓库解压在 `D:\code\jiulv-kb`。

```powershell
cd D:\code\jiulv-kb

# 1. 建虚拟环境（不要用全局 Python，依赖版本容易打架）
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# 如果提示「禁止运行脚本」，先执行：
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. 装依赖。带 parsers 才能读 docx / pdf / xlsx
python -m pip install -U pip
python -m pip install -e ".[parsers]"

# 3. 生成配置
python -m jiulv.cli init

# 4. 自检。这一步的输出后面所有排查都要用，先存一份
python -m jiulv.cli doctor

# 5. 探测资料库 → 建索引
python -m jiulv.cli connect
python -m jiulv.cli sync

# 6. 起服务，浏览器打开 http://127.0.0.1:8787
python -m jiulv.cli serve
```

关于入口写法：`pip install -e .` 之后会生成 `jiulv.exe`，正常情况下直接敲
`jiulv sync` 就行。但如果 PATH 没刷新生效，或者被公司的 Python 环境拦住，
**统一用 `python -m jiulv.cli <子命令>`**，这个写法在所有环境下都成立，
本仓库的测试也是这么调的。文档里两种都出现，是同一个东西。

资料不在默认位置时，用环境变量把根目录指过去，不用改代码：

```powershell
$env:JIULV_WB_ROOT = "D:\work\WorkBuddy"
$env:JIULV_OC_ROOT = "D:\work\.openclaw"
python -m jiulv.cli connect --dry-run    # 先看探测到什么，不写文件
python -m jiulv.cli connect
```

---

## 三、目录地图：改东西之前先看这里

```
jiulv/
  cli.py            命令行入口，所有子命令的参数与输出都在这里
  config.py         读 config.yaml + sources.yaml，合并默认值，展开 ~ 与环境变量
  platform_compat.py 跨平台差异（编码、路径同一性、原子写、文件名、遍历）
  connectors.py     探测本机有哪些资料库，写 sources.yaml
  sync.py           指纹判断要不要重扫；watch 常驻轮询
  ingest.py         扫描 → 解析 → 抽元数据 → 建 FTS 索引 → 重算注解与图谱
  parsers.py        md/txt/docx/pdf/xlsx → 纯文本
  extract.py        元数据抽取、类型判定、片段截取
  tokenizer.py      中文 bigram 分词（unicode61 不切中文，这是必须的）
  search.py         BM25 检索、文档聚合、副本折叠、可选向量混合
  annotate.py       「这篇为什么这么写」的注解抽取
  graph.py          概念抽取、双向链接、子图
  compose.py        组稿上下文包
  inbox.py          回写池：agent 写完的稿子落盘再入库
  db.py             建表、连接、统计
  api.py            REST 接口
  mcp_server.py     MCP（stdio）
  doctor.py         环境自检
  web/              静态前端（index.html / app.js / style.css）
```

改需求时按这个顺序找：**行为不对先看对应的那个 py，界面不对只看 `web/`。**
`web/` 里没有框架、没有编译，改完刷新浏览器就生效，但注意 `index.html` 里
引用 `app.js?v=N` 带版本号，改了 js 要顺手把 N 加一，否则浏览器会用缓存。

---

## 四、必须守住的五条不变量

改代码时这几条一旦破了，症状都不明显，但会慢慢把库搞坏。

**一、文件是唯一真相。** 索引只是缓存，任何时候都可以删掉重建。
不要把「用户手工订正的内容」只存在数据库里——下次 `sync` 会被文件内容覆盖。
需要持久化的东西写成 md 放进回写池（`data/inbox/`）。

**二、回写走同一条流水线。** `POST /api/v1/inbox` 不是直接往 `docs` 表插一行，
而是写一个 md 文件再正常收录。这样解析、元数据、注解、图谱全都自动生效。
不要为了「快一点」改成直接 insert，会出现「接口写进去的文档没有注解」的半成品。

**三、收录范围只有 include / exclude 一处判定。** `iter_source_files()` 同时被
收录、指纹、界面计数三处调用。改了遍历逻辑要保证三处仍然一致，
否则会出现「界面显示 800 篇、入库 600 篇」。

**四、注解与图谱是可重算的派生数据。** 它们由 `docs.content` 现算，
不读盘、不联网。全量重算一万多条注解约 6 秒，所以 `ingest` 只在真有增删改时才跑。
在监听模式下每轮都重算是主要的性能坑。

**五、所有落盘的文本用 utf-8 + LF。** 见 `platform_compat.atomic_write_text`。
内容哈希参与增量判断，换行符一变，全库会被当成「都改过了」重收一遍。

---

## 五、平台敏感点清单

Windows 上的问题全是「不报错但结果不对」这一类，比崩溃难查。以下每一处都是
已经处理过的，改动这些代码时要明白为什么这么写。

### 1. 控制台编码 —— `platform_compat.setup_console()`

Python 在 Windows 上直接写控制台走的是 WriteConsoleW，中文没问题。但输出一旦
被重定向（`> out.txt`、被别的程序当子进程、IDE 的终端面板），就退回系统代码页：
简体中文机器是 cp936，英文机器是 cp437。打个「—」或「·」就 UnicodeEncodeError，
整条命令失败。

处理：`cli.main()` 第一件事就是把 stdout/stderr 重配成 utf-8 + errors=replace。
测试里对应 `test_platform.py::test_write_stdio_line_uses_lf_and_utf8` 一带。

**改坏了什么症状**：`jiulv search 三亚 > a.txt` 报
`UnicodeEncodeError: 'cp936' codec can't encode character`。

### 2. MCP 的 stdio —— `platform_compat.read_stdio_lines / write_stdio_line`

这是最阴的一个。`sys.stdin` 在 Windows 默认按代码页解码，而 MCP 客户端送来的是
UTF-8 JSON。`{"query": "三亚 亲子"}` 会被解成乱码，`json.loads` 可能不报错，
于是检索词错了、结果不对，但日志里一切正常。
写出去的方向，文本模式会把 `\n` 翻成 `\r\n`，严格解析的客户端会多看到一个 `\r`。

处理：绕过文本层，直接读写 `sys.stdin.buffer` / `sys.stdout.buffer`，自己解码。

**改坏了什么症状**：MCP 工具能调通，但带中文的查询返回空结果或乱码；
或者客户端报 JSON 解析失败。

### 3. 路径同一性 —— `platform_compat.path_key()`

Windows 路径大小写不敏感、两种斜杠都认。`C:\Data\KB` 与 `c:/data/kb/`
是同一个目录，字符串却不相等。早年用 `os.path.realpath` 直接比字符串，
后果是同一批文件被当成两个来源收两遍，界面显示「连了 14 个库，其中 3 个是同一个」。

`path_key` = realpath + normcase。用在三处：`config._merge_sources` 去重、
`connectors` 判断某个库是否已连、`ingest._prune` 判断文档是否还在。

`_prune` 还多做了一层 `.lower()` 折叠，因为 macOS 默认文件系统同样大小写不敏感。
只折叠不删除是安全的（最多漏删一个幽灵条目），反过来会误删整库重建。

**改坏了什么症状**：`sync` 每次都报「已同步 +1800 篇」；或者 connect 反复
把同一个目录写成两条来源。

### 4. 目录遍历 —— `platform_compat.walk_files()`

原来用 `Path.rglob("*")`。Python 3.12 之前它会跟进目录联结（junction），
Windows 上 `C:\Users\你\AppData\Local` 里就有成环的联结，一进去无限递归，
进程卡死或爆栈。

改用 `os.walk(followlinks=False)`，并且按层排序，保证每次遍历顺序一致
（顺序不稳定会让增量判断抖动）。

**改坏了什么症状**：`sync` 卡在某个占 CPU 的循环里不动，杀不掉；
或者扫描篇数每次都不一样。

### 5. 文件名合法性 —— `platform_compat.safe_filename()`

回写池要用标题当文件名。标题里出现半角冒号（「三亚:怎么选房」）、斜杠、
尖括号，在 Windows 上直接 OSError。还有三个坑：`CON` / `NUL` / `COM1` 是设备名，
带扩展名也打不开；结尾的点和空格会被静默截掉，写进去的名字和以为的不一样；
长度要限。

**改坏了什么症状**：`POST /api/v1/inbox` 返回 500，报
`OSError: [Errno 22] Invalid argument`。

### 6. 原子写与文件占用 —— `platform_compat.atomic_write_text()`

两件事：一是先写临时文件再 `os.replace`，中途失败不会留下半个状态文件；
二是 Windows 上 `os.replace` 会因为目标正被杀毒软件或别的进程打开而抛
`PermissionError`，这是常态，退让重试几次就过去了。

新路（`.tmp`）留在同目录，保证 replace 是同一分区内的原子操作。

**改坏了什么症状**：偶发 `PermissionError: [WinError 5]`；或者
`data/sync_state.json` 变成半个 JSON，下一轮 sync 白扫全库。

### 7. SQLite 等锁 —— `db.connect()`

`sqlite3.connect(timeout=30)` + `PRAGMA busy_timeout=30000`。默认 5 秒在 Windows
上不够，杀毒软件会短暂独占刚写完的 `-wal` 文件，表现为 `database is locked`。

**改坏了什么症状**：`serve` 和 `watch` 同时跑时，其中一个偶尔报
`sqlite3.OperationalError: database is locked`。

### 8. 系统垃圾文件 —— `platform_compat.is_os_junk()`

`~$2026预算.docx` 是 Office 的锁文件，`Thumbs.db` / `desktop.ini` 是资源管理器的
缓存。它们会命中 `**/*.docx` 之类规则被收进来，解析必然失败，在「解析失败」列表
里刷屏，还会让目录指纹每开一次文档就变一次。

---

## 六、Windows 上会碰到的故障，按症状查

**页面打不开，或打开是空白**
`jiulv serve` 没起，或者端口被占。先 `netstat -ano | findstr :8787` 看谁占了。
换端口：`python -m jiulv.cli serve --port 8800`。注意改端口后浏览器要跟着换。

**页面能开但列表是空的、来源页显示全部「断开」**
`config.yaml` 里没有来源，或者 sources.yaml 里的路径是 macOS 的绝对路径
（从别的机器拷过来的）。跑 `jiulv doctor`，看「来源」那几行：
显示 `[断]` 的就是路径不存在。把 `sources.yaml` 删掉重跑 `jiulv connect`，
或在 `config.yaml` 里手写 `sources`（手写的永远优先）。

**`connect` 什么都没探测到**
默认在两处找：`%USERPROFILE%\WorkBuddy\*\小红书酒旅资料库` 和
`%USERPROFILE%\.openclaw\workspace\reference`。资料在这两处之外时，
设置 `JIULV_WB_ROOT` / `JIULV_OC_ROOT`。`--dry-run` 只探测不写文件，先用它确认。

**`sync` 报 `database is locked`**
有两个进程在写同一个库（常见是 `serve` 和 `watch` 同时跑）。等 30 秒重试，
或者先停掉一个。不要在 OneDrive 同步目录里放 `data/`，见下面第七节。

**`OSError: [Errno 22] Invalid argument` 或 `[WinError 123]`**
路径里有 Windows 不认的字符，或者路径超过 260 字符。`jiulv doctor` 会对
路径过长的来源打 `[路径过长]` 标记。处理办法见第七节的长路径一节。

**`OSError: [WinError 206] 文件名或扩展名太长`**
同一个原因，长路径。要开 `LongPathsEnabled`。

**中文变成一堆问号或方块**
控制台代码页不对。`chcp 65001` 临时解决；持久化要开「使用 Unicode UTF-8 提供
全球语言支持」，或者每次命令前 `$env:PYTHONUTF8 = "1"`。
注意：这个设置影响的是**显示**，不影响入库内容（入库固定 utf-8）。

**装了 python-docx 但 docx 还是被跳过**
`jiulv doctor` 的「解析依赖」那行会写缺哪个。多半是装到了另一个 Python 里
（`pip` 和 `python` 不是同一个环境）。统一用 `python -m pip install`，
不要直接敲 `pip`。

**从 macOS 拷过来的库，文档标题全是问号**
SQLite 文件本身是 utf-8 的，问号说明是终端显示问题，不是数据问题。
用 `jiulv doctor` 的「中文输出」一行确认。

---

## 七、Windows 特有的三个坑（不是代码问题，是环境问题）

**一、别把 data/ 放进 OneDrive。**
Windows 上「桌面 / 文档 / 图片」默认被 OneDrive 接管并实时同步。
SQLite 的 WAL 模式需要三个文件（`kb.sqlite3`、`-wal`、`-shm`）保持一致，
OneDrive 逐文件同步会让它们错位，轻则 `database is locked`，重则文件损坏。

项目目录请放在 `D:\code\` 这类不被同步的位置。已经放错的话：
停掉服务 → 把整个 `data/` 移出来 → 重新 `jiulv sync --force`（会重建索引，几分钟）。

**二、网络盘和 WSL 的跨系统路径不能用。**
`\\NAS\共享\kb.sqlite3` 这类 UNC 路径不支持 WAL，SQLite 会直接拒绝。
WSL 的 `/mnt/c/...` 由 9p 文件系统提供，锁语义不完整，同样不要用。
要多人共用，正确做法是各自本地一份库，用 git 同步 `docs/` 里的素材，不要共用一个 sqlite。

**三、长路径。**
Windows 默认单条路径上限 260 字符。这个项目的资料库常常是
`D:\资料\2026\小红书酒旅\某批次\02_地区素材库\02_海南\三亚\某酒店\某笔记.md`
这种深度，叠加中文（一个汉字算一个字符，没问题）容易撞线。

开长路径支持（管理员 PowerShell，改完要重启）：

```powershell
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
```

`jiulv doctor` 会直接告诉你有没有开。

---

## 八、怎么验证一次改动

四道关，从便宜到贵。改完至少跑到第三道。

**第一道：语法与自检**

```powershell
python -m compileall -q jiulv
python -m jiulv.cli doctor
```

**第二道：单元测试**（约 9 秒，75 个用例，不碰真实资料库）

```powershell
python -m pytest tests/ -q
```

测试全部用临时目录造小语料，clone 下来就能跑，不需要先建库。
如果在 Windows 上看到 `test_walk_files_does_not_follow_symlink_loops` 被 skip，
是因为建符号链接需要开发者模式，属于正常。

**第三道：真实库冒烟**

```powershell
python -m jiulv.cli sync                 # 应该报「无变化」或小幅增量
python -m jiulv.cli stats                # 文档数、注解数、概念数
python -m jiulv.cli search 三亚 亲子 --limit 3
```

**第四道：接口与 MCP**

```powershell
# 另开一个窗口，保持 serve 运行
curl.exe http://127.0.0.1:8787/api/v1/health
curl.exe "http://127.0.0.1:8787/api/v1/search?q=%E4%B8%89%E4%BA%9A&limit=3"

# MCP 冒烟：一行 initialize 应该立刻回 serverInfo
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m jiulv.cli mcp
```

MCP 那行如果回的 JSON 带中文乱码，或者报解析失败，说明碰到了第五节第 2 条，
回去看 `platform_compat.read_stdio_lines`。

**改前端的话**：`jiulv/web/` 没有构建。改完把 `index.html` 里 `app.js?v=N`
的 N 加一，刷新浏览器，按 F12 看 Console 有没有报错。

---

## 九、给接手 AI 的协作约定

**动手前先跑 `jiulv doctor`，把输出留住。** 这一段包含版本、路径、依赖、
库状态，后面任何判断都要基于它，不要凭猜测。

**不要为了「让 Windows 能跑」加特判。** 需要区分平台的地方已经全在
`platform_compat.py` 里了。新增平台差异应该往那个文件里加函数，
不要在业务代码里散落 `if sys.platform == "win32"`。

**改完记得跑测试。** 尤其是动了 `ingest.py`、`config.py`、`connectors.py`
的时候，那三个文件被四五个模块依赖。

**别动 `data/` 里的东西。** 那是用户真实数据的索引，`kb.sqlite3` 一百多兆。
要试验就 `jiulv init D:\tmp\t` 另外建一个空目录，在那边跑。

**提交信息写「为什么」，不写「改了什么」。** 改了什么看 diff 就有，
为什么这么改只有你知道，与平台相关的取舍尤其要写下来。

**如果发现这份文档里某条是错的，先改文档再改代码。** 这份文档的价值就在于
它是 Windows 上唯一可信的现状描述。

---

## 十、还没在 Windows 上实测过的部分（诚实清单）

这些代码在 macOS 上验证过，Windows 上的行为是按文档推理的，没有实机跑过。
在 Windows 上接手时，第一优先是把这几条补测，测完把结论回写进这一节。

一、`jiulv serve` 长时间常驻（数小时）后是否有内存增长，uvicorn 在 Windows
上的信号处理与 Ctrl-C 退出是否干净。

二、`jiulv watch` 用 PowerShell 后台任务（`Start-Job`）跑时，进程生命周期是否
符合预期，父进程退出后会不会变成孤儿。

三、`atomic_write_text` 在杀毒软件（尤其是开了实时防护的 Windows Defender）
下的重试次数是否够。目前是 6 次、间隔 0.06 到 0.36 秒。如果实测仍偶发
`PermissionError`，把 `retries` 提到 10 并考虑指数退避。

四、超长路径（250 字符以上）在开启 `LongPathsEnabled` 之后，
`os.walk` 与 `Path.stat` 是否都正常。Python 3.6 以后对长路径的支持依赖
`\\?\` 前缀的自动添加，理论上没问题，但没实测。

五、中文路径下的 UTF-8 解码：`read_text_file` 的回退顺序是
utf-8 → utf-8-sig → gb18030 → latin-1。记事本另存的「ANSI」在简体中文机器上
是 GBK，理论上能兜住，实际样例待验证。

六、MCP 接到 WorkBuddy / Claude Desktop 这类客户端时的路径配置。
Windows 下客户端常要求 `command` 写 `python.exe` 绝对路径并单独给 `cwd`：

```json
{
  "mcpServers": {
    "jiulv": {
      "command": "D:\\code\\jiulv-kb\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jiulv.cli", "mcp"],
      "cwd": "D:\\code\\jiulv-kb"
    }
  }
}
```

（JSON 里的反斜杠要转义成 `\\`，这一点很多配置示例都写错。）
