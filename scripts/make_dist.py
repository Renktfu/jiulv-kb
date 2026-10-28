#!/usr/bin/env python3
"""把仓库打成一个能直接拷走的分发包。

产出结构（默认落在仓库的上一级，不在仓库里）：

    dist/
    ├── 先读我.txt
    ├── macos/          苹果电脑用这份，双击「双击启动.command」
    │   ├── 双击启动.command
    │   ├── jiulv/  tests/  docs/  scripts/  examples/
    │   ├── pyproject.toml  README.md  LICENSE  ...
    │   └── data/       空壳，第一次启动会把索引建在这；library/ 里带拆好的自有资料
    └── windows/        Windows 用这份，双击「双击启动.bat」
        └── （同上，启动器换成 .bat，另附 Windows 安装说明）

刻意不带的东西，以及为什么：

- `.venv/`：虚拟环境里全是绝对路径，换目录就废。启动器第一次运行会自己建。
- `data/` 整个不进包，只留空壳和一个例外：`data/library/` 是拆好篇的自有资料
  （马尔代夫 22 篇），不带索引、不带客户信息，带上让新包开箱就有东西可搜。
- `data/kb.sqlite3`：160MB 真实索引，含客户酒店名，且和你本机路径绑死。
  新环境第一次启动会自动建库（1500 篇约半分钟）。
- `config.yaml` / `sources.yaml`：里面是本机绝对路径。没有它们反而更好——
  启动时 `jiulv connect` 会按当前机器的实际布局重新探测。
- `.git/`：分发包不是仓库。要版本管理就用仓库本身。
- `__pycache__` / `.pytest_cache` / `*.egg-info`：构建残留。

用法：

    python3 scripts/make_dist.py                 # 生成 <仓库的上一级>/dist/
    python3 scripts/make_dist.py --out ~/Desktop # 换输出位置
    python3 scripts/make_dist.py --clean         # 先清空再生成
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

# 默认写在仓库外面（仓库的上一级）。理由是这份东西的用途是「拷走」，
# 不是「留在仓库里」：放外面不会被 git 状态打扰，也不会被 make clean 顺手删掉。
DEFAULT_OUT = ROOT.parent / "dist"

# 打进包里的东西。白名单而不是黑名单：新增文件如果忘了加进来，
# 打包结果是「少一个文件」；用黑名单则会「多带一份真实数据」，后果更糟。
PAYLOAD = [
    "jiulv",
    "tests",
    "examples",
    "docs",
    "scripts",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "Makefile",
    "config.example.yaml",
]

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git", ".idea", ".vscode", "data"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_SUFFIX = (".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _ignore(directory, names):
    """shutil.copytree 要的是「忽略哪些」，不是「保留哪些」。

    这里只返回该丢的名字。写反过一次：返回了保留列表，结果打包出来的
    包里只剩 __pycache__ 的 pyc，一行源码都没有——而且体积看着正常，
    不打开看根本发现不了。
    """
    dropped = []
    for name in names:
        if name in SKIP_DIRS or name in SKIP_FILES:
            dropped.append(name)
        elif name.endswith(SKIP_SUFFIX) or name.endswith(".egg-info"):
            dropped.append(name)
    return dropped


def write_text_crlf(path: Path, text: str, bom: bool = False) -> None:
    """写一个 Windows 上能正常打开的中文文本。

    两件事得自己做，不能靠 Path.write_text：

    一是换行。记事本从 Windows 10 1809 起才认孤立的 LF，再老一点的版本
    会把整段并成一行。统一 CRLF，两个平台都正常。
    （Path.write_text 的 newline 参数是 3.10 才有的，本项目支持 3.9。）

    二是 BOM。UTF-8 无 BOM 的 txt 在 1903 之前的记事本里会按 ANSI 打开，
    满屏乱码——用的人第一反应是「文件坏了」。加上 BOM，记事本、写字板、
    VS Code 全认。
    """
    body = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = body.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def copy_payload(dest: Path) -> int:
    """复制程序本体，返回复制的文件数。"""
    count = 0
    for item in PAYLOAD:
        src = ROOT / item
        if not src.exists():
            say(f"  跳过（源里没有）：{item}")
            continue
        target = dest / item
        if src.is_dir():
            shutil.copytree(src, target, ignore=_ignore)
            count += sum(1 for p in target.rglob("*") if p.is_file())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            count += 1

    # data/ 只留一个空壳。没有它，第一次启动时 SQLite 会自己建目录，
    # 但如果用户把包放在只读位置，报错会出现在很深的地方，不好查。
    #
    # 例外是 data/library：那里面是拆好篇的自有资料（目前是马尔代夫 22 篇），
    # 不是索引，也不含客户信息，带上能让新包开箱就有内容可搜。
    data_dir = dest / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / ".gitkeep").write_text("", encoding="utf-8")
    library_src = ROOT / "data" / "library"
    if library_src.is_dir():
        shutil.copytree(library_src, data_dir / "library", dirs_exist_ok=True, ignore=_ignore)
    return count


def write_launcher(dest: Path, name: str) -> None:
    """把启动器复制到包根，让人不用进 scripts/ 找。"""
    src = ROOT / "scripts" / name
    if not src.exists():
        return
    target = dest / name
    raw = src.read_bytes().replace(b"\r\n", b"\n")
    if target.suffix.lower() == ".bat":
        # cmd 的老解析器对孤立 LF 有已知问题（尤其是括号块里的多行 if）。
        # 仓库里存 LF 便于 diff，打包时换成 CRLF。
        raw = raw.replace(b"\n", b"\r\n")
    target.write_bytes(raw)
    if os.name != "nt":
        target.chmod(0o755)


README_TOP = """酒旅爆款文案知识库 — 分发包
================================

这个文件夹里有两份程序，按你面前这台电脑挑一份用，另一份可以直接删：

    macos/      苹果电脑（macOS）
    windows/    Windows 电脑

两边是同一套代码，只有启动器不同。挑好之后进对应文件夹，双击里面的
「双击启动」文件就行，第一次会自动装依赖、建索引、打开网页，
大约一到两分钟，之后就快了。

这份包不带你自己的数据。资料库的路径、已建好的索引都没打进来，
所以换一台机器、换一个目录都能直接跑，不会互相干扰。
第一次启动时程序会自己去探测本机的 WorkBuddy / OpenClaw 资料库，
探测不到就让你手动指定，不会静默建一个空库。

详细说明在各自的 docs/ 里：

    docs/WINDOWS.md       Windows 安装、排查、以及给接手的人看的交接说明
    docs/CONNECT.md       资料库怎么接进来
    docs/API.md           接口清单（给 AI 用的）
    docs/INTEGRATIONS.md  接入 OpenClaw / Codex / WorkBuddy 的样例

遇到问题先跑这一句，把输出整段发出来就能定位：

    macos:    .venv/bin/python -m jiulv.cli doctor
    windows:  .venv\\Scripts\\python.exe -m jiulv.cli doctor
"""

MACOS_README = """macOS 使用说明
================

第一次用
--------
1. 双击「双击启动.command」。
   如果提示「无法打开，因为来自身份不明的开发者」，右键它 →
   选「打开」→ 再点「打开」。只需要做一次。
2. 会弹出一个终端窗口，自动装依赖（约一分钟），然后建索引、
   打开浏览器。窗口别关，关掉服务就停了。
3. 想让它开机自动跑，在那个终端里按 Ctrl+C 停掉，然后执行：

       .venv/bin/python -m jiulv.cli autostart on

   不想自启了就 `autostart off`，一条命令卸干净。

以后每次
--------
双击「双击启动.command」即可。已经在跑的话，它只会帮你把浏览器再打开一次，
不会报错。端口被别的程序占了会自动往后顺延一个。

命令行用法
----------
    .venv/bin/python -m jiulv.cli start        日常启动（网页 + 后台同步）
    .venv/bin/python -m jiulv.cli sync         只同步一次资料库
    .venv/bin/python -m jiulv.cli doctor       环境体检，出问题先跑这个
    .venv/bin/python -m jiulv.cli connect     重新探测资料库
    .venv/bin/python -m jiulv.cli --help      全部命令

资料怎么更新
------------
不用重启，也不用管索引，三种情况各一条路：

  新笔记        丢进已经接过的资料库目录，两分钟内自己进来
  整份新资料    拆成单篇放进 data/library/<分类>/，再跑一次 sync
  改过或删掉    改完删完同样跑一次 sync，索引跟着变

     .venv/bin/python -m jiulv.cli sync      立刻同步一次
     .venv/bin/python -m jiulv.cli sources   每个目录多少篇、上次什么时候扫的

还没接自己的目录？先跑 connect，或者照 config.yaml 里注释的样子自己加一条。
一个来源都没接的时候 sync 会直接说「还没有接任何资料目录」，不会假装成功。

数据在哪
--------
    data/kb.sqlite3     索引（删掉会重新建，不会丢原始文件）
    data/inbox/         AI 写完回写的稿子
    data/logs/          自启时的运行日志

原始资料一篇都不会被复制进来，程序只记路径、只读。删这个文件夹
不会影响你的任何素材。

常见问题
--------
双击后窗口一闪而过
    在终端里手动跑 `python3 scripts/setup.py --check`，它会说清缺什么。

提示找不到 python3
    brew install python@3.12，或去 python.org 下安装包。

页面打开是白屏
    服务没起来。看终端里的报错，或跑 doctor。最省事的办法是关掉窗口重开一次。

依赖装不上（网络）
    JIULV_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \\
      python3 scripts/setup.py

想换端口
    在文件夹里建 config.yaml，写：

        app:
          port: 8800
"""

WINDOWS_README = """Windows 使用说明
=================

第一次用
--------
1. 装 Python（只需一次）。
   去 https://www.python.org/downloads/windows/ 下载 3.9 以上的安装包。
   安装的第一步一定要勾选 **Add python.exe to PATH**，这个不勾后面全是坑。
   装完最好重开一次资源管理器，让 PATH 生效。

2. 双击「双击启动.bat」。
   会开一个黑色命令行窗口，自动装依赖（约一分钟），然后建索引、
   打开浏览器。**这个窗口别关**，关掉服务就停了。开着它用就行，
   可以最小化。

3. 想开机自动跑（可选）。在窗口里按 Ctrl+C 停掉，然后执行：

       .venv\\Scripts\\python.exe -m jiulv.cli autostart on

   它会在「登录时」建一个计划任务，不需要管理员权限。
   卸载：autostart off。想彻底删干净：schtasks /Delete /TN jiulv-kb /F

以后每次
--------
双击「双击启动.bat」。已经在跑的话只会帮你把浏览器再打开一次。

三条容易踩的环境问题
--------------------
一、别把整个文件夹放在 OneDrive / 坚果云 / 网络盘里。
    数据库用的是 WAL 模式，三个文件必须同进同出，同步盘会把它拆散，
    轻则 database is locked，重则文件损坏。放 C:\\ 或 D:\\ 的普通目录下。

二、路径别太深。
    Windows 默认限制 260 个字符，包一层层嵌套很容易超。
    真超了就跑一次（管理员 PowerShell）：

        New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" \\
          -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force

三、路径和文件名别用中文标点。
    程序自己会把回写文件名里的非法字符换掉，但你手写 config.yaml
    的时候如果用了全角引号，解析会失败。

命令行用法
----------
本窗口里（或新开 cmd 并 cd 到这个文件夹）：

    .venv\\Scripts\\python.exe -m jiulv.cli start        日常启动
    .venv\\Scripts\\python.exe -m jiulv.cli sync         只同步一次
    .venv\\Scripts\\python.exe -m jiulv.cli doctor       环境体检，出问题先跑
    .venv\\Scripts\\python.exe -m jiulv.cli --help       全部命令

资料怎么更新
------------
不用重启，也不用管索引，三种情况各一条路：

  新笔记        丢进已经接过的资料库目录，两分钟内自己进来
  整份新资料    拆成单篇放进 data\\library\\<分类>\\，再跑一次 sync
  改过或删掉    改完删完同样跑一次 sync，索引跟着变

    .venv\\Scripts\\python.exe -m jiulv.cli sync      立刻同步一次
    .venv\\Scripts\\python.exe -m jiulv.cli sources   每个目录多少篇、上次什么时候扫的

还没接自己的目录？先跑 connect，或者照 config.yaml 里注释的样子自己加一条。
一个来源都没接的时候 sync 会直接说「还没有接任何资料目录」，不会假装成功。

数据在哪
--------
    data\\kb.sqlite3     索引（删掉会重新建，不会丢原始文件）
    data\\inbox\\        AI 写完回写的稿子
    data\\logs\\         自启时的运行日志

原始资料一篇都不会被复制进来，程序只记路径、只读。

常见问题
--------
双击后窗口一闪而过 / 报 'python' 不是内部或外部命令
    没装 Python，或者装的时候没勾 Add to PATH。重装一次并勾上。

ModuleNotFoundError: No module named 'yaml'
    挑到了另一个 Python。在窗口里跑 `py scripts\\setup.py --check`，
    它会告诉你用的是哪个解释器、缺什么。

页面白屏
    服务没起来，看窗口里的报错。或跑 `.venv\\Scripts\\python.exe -m jiulv.cli doctor`。

database is locked
    多半是文件夹放在同步盘里了，或者杀软在扫 data 目录。挪到本地普通目录。

更多
----
docs\\WINDOWS.md 是给「在这台机器上接手这个项目的人或 AI」看的，
里面有目录地图、平台敏感点、按症状排查的故障表，改代码之前先看那份。
"""


def build(out: Path, clean: bool = False) -> int:
    if clean and out.exists():
        say(f"清空 {out}")
        shutil.rmtree(out, ignore_errors=True)

    say(f"输出目录：{out}")
    out.mkdir(parents=True, exist_ok=True)

    total = {}
    for platform, launcher, readme, title in (
        ("macos", "jiulv-start.command", MACOS_README, "macOS 版"),
        ("windows", "jiulv-start.bat", WINDOWS_README, "Windows 版"),
    ):
        dest = out / platform
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        count = copy_payload(dest)
        write_launcher(dest, launcher)
        write_text_crlf(dest / "使用说明.txt", readme, bom=True)
        total[platform] = count
        say(f"  {title}：{count} 个文件 → {dest}")

    write_text_crlf(out / "先读我.txt", README_TOP, bom=True)
    say(f"  顶层说明：先读我.txt")

    say()
    for platform, count in total.items():
        size = sum(p.stat().st_size for p in (out / platform).rglob("*") if p.is_file())
        say(f"  {platform:8s} {count:4d} 个文件  {size / 1024 / 1024:.1f} MB")
    say()
    say("完成。两个目录各自独立，可以只拷其中一个走。")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成分发包（macos/ 与 windows/）")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"输出目录，默认 {DEFAULT_OUT}",
    )
    parser.add_argument("--clean", action="store_true", help="先清空输出目录")
    args = parser.parse_args(argv)
    return build(Path(args.out).expanduser().resolve(), clean=args.clean)


if __name__ == "__main__":
    raise SystemExit(main())
