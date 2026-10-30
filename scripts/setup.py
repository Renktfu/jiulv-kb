#!/usr/bin/env python3
"""启动前的自举：把依赖装进项目自带的 .venv，然后把参数交给 jiulv start。

为什么需要这个文件：

分发包不能带 .venv。虚拟环境里写满了绝对路径（bin/python 的 shebang、
pyvenv.cfg 的 home、每个 console_scripts 的第一行），换个目录、换台机器立刻废。
所以第一次必须现场建。

原来的两个双击脚本各自写了一段「找解释器 → 发现缺依赖 → 打印 pip 命令 →
让用户自己去敲」，等于把唯一的门槛留在了第一次。现在把这段收进一个纯标准库
脚本，两个平台的启动器都只负责「找到 python，然后跑我」，逻辑只有一份。

用法（启动器就是这么调的）：

    python3 scripts/setup.py                # 装好依赖，然后 jiulv start
    python3 scripts/setup.py --port 8790    # 多余参数原样传给 jiulv start
    python3 scripts/setup.py --check        # 只体检，不动任何东西
    python3 scripts/setup.py --rebuild-env  # 删掉 .venv 重建（依赖装坏时用）
    python3 scripts/setup.py -- <子命令>     # 跑别的子命令，如 -- doctor

环境变量：

    JIULV_PIP_INDEX   自定义 pip 源。不设时先走默认源，失败自动用清华镜像重试一次
    JIULV_NO_INSTALL=1 只检查不安装（CI 或离线环境用）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 上输出被重定向时用的是系统代码页（中文机器 cp936），
# 打一个「—」就 UnicodeEncodeError 把整个流程打断。这里先兜住。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
MIN_PYTHON = (3, 9)

# 缺这三个跑不起来。解析器三个是可选的——不装只是 docx/pdf/xlsx 会被跳过。
REQUIRED = ("yaml", "fastapi", "uvicorn")
OPTIONAL = ("docx", "pypdf", "openpyxl")

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def venv_python(root: Path = VENV_DIR) -> Path:
    """虚拟环境里解释器的位置。Windows 在 Scripts，其他在 bin。"""
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def which_python() -> str:
    """启动器挑出来的那个 python 是怎么调用的。

    `py -3` 是两个词，不能当路径用，所以这里存成命令前缀列表。
    """
    return sys.executable or "python3"


def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_python() -> bool:
    if sys.version_info[:2] < MIN_PYTHON:
        say(
            f"Python 版本太低：现在是 {sys.version.split()[0]}，"
            f"需要 {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上。"
        )
        say("Windows 去 python.org 下新版安装包，勾选 Add python.exe to PATH；")
        say("macOS 用 brew install python@3.12，或去 python.org 下安装包。")
        return False
    return True


def missing_modules(py, names) -> list:
    """一次性问完，比逐个 import 快，也避免把 import 异常当成「没装」。"""
    code = "import " + ", ".join(names)
    result = _run([str(py), "-c", code], timeout=90)
    if result.returncode == 0:
        return []
    if "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr:
        return list(names)
    # 装是装了但导入就炸（版本冲突、缺动态库），也算不能用
    return list(names)


def venv_is_usable(py: Path, root: Path = VENV_DIR) -> bool:
    """这个 .venv 是不是「这台机器、这个位置」建出来的。

    分发包被人先在原处双击过一次，.venv 就跟着拷给别人了。里面的 bin/python
    是软链、pyvenv.cfg 的 home 指向前一台机器的解释器、site-packages 也按旧
    路径找。这时 py.exists() 是真的，但一跑就是「bad interpreter」或者一片
    ModuleNotFoundError，用户只看到窗口闪一下就没了，猜不到该重建虚拟环境。

    判据只有一个：它自己报的 sys.prefix 必须就是当前这个 .venv 目录。
    """
    if not py.exists():
        return False
    code = "import os, sys; print(os.path.realpath(sys.prefix))"
    try:
        result = _run([str(py), "-c", code], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    reported = (result.stdout or "").strip()
    return bool(reported) and reported == os.path.realpath(root)


def ensure_venv(rebuild: bool = False) -> Path:
    py = venv_python()
    if rebuild and VENV_DIR.exists():
        say(f"重建虚拟环境：删掉 {VENV_DIR}")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    if py.exists():
        if venv_is_usable(py):
            return py
        say("现有的 .venv 不是这个目录建的（多半是从别处拷过来的包），重建。")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    say(f"第一次运行，正在创建虚拟环境：{VENV_DIR}")
    result = _run([which_python(), "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0 or not py.exists():
        detail = (result.stderr or result.stdout or "").strip()
        say("创建虚拟环境失败。")
        if detail:
            say(detail[-800:])
        if sys.platform.startswith("linux"):
            say("Debian / Ubuntu 上通常缺这个包：sudo apt install python3-venv")
        else:
            say("试试直接用安装包装一遍 Python，或手动执行：")
            say(f"  {which_python()} -m venv {VENV_DIR}")
        raise SystemExit(1)
    return py


def _index_attempts():
    """装东西时的源，按顺序试。"""
    custom = os.environ.get("JIULV_PIP_INDEX")
    if custom:
        return [(f"你指定的源 {custom}", ["-i", custom])]
    return [("默认源", []), (f"清华镜像 {MIRROR}（默认源失败，自动重试）", ["-i", MIRROR])]


def upgrade_build_tools(py: Path) -> None:
    """先把 pip / setuptools / wheel 升到能用 pyproject.toml 的版本。

    这不是洁癖。Python 3.9 的 venv 自带 setuptools 58，不支持 PEP 660 的
    editable 安装，于是 `pip install -e .` 会退回老的 setup.py 路径，然后报
    「File "setup.py" or "setup.cfg" not found」——一个完全看不出真实原因的错误。
    实测就是这个，所以装项目之前先补这一步。

    升级失败不拦：老 setuptools 装不了 editable，后面会失败并给出真实报错，
    那时候的日志比这里一句「升级失败」有用。
    """
    for _label, extra in _index_attempts():
        result = subprocess.run(
            [
                str(py), "-m", "pip", "install", "--disable-pip-version-check",
                "--quiet", "--upgrade", "pip", "setuptools", "wheel",
            ]
            + extra,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return


def pip_install(py: Path, spec: str) -> bool:
    """装依赖。默认源失败后自动用国内镜像再试一次。

    这件事不该藏起来：日志里明确写出换了源，用户以后自己装东西才知道该加 --index-url。
    """
    base = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
    for label, extra in _index_attempts():
        say(f"安装依赖（{label}），第一次大约一分钟…")
        result = subprocess.run(
            base + extra + ["-e", spec],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        tail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()[-6:]
        say("失败，末尾几行：")
        for line in tail:
            say("  " + line)
    return False


def ensure_deps(py: Path, allow_install: bool = True) -> bool:
    missing = missing_modules(py, REQUIRED)
    optional_missing = missing_modules(py, OPTIONAL)
    if not missing and not optional_missing:
        return True

    if not missing and optional_missing:
        say(f"提示：{', '.join(optional_missing)} 没装，这些格式会被跳过（不影响使用）")

    if not allow_install:
        say(f"依赖不全：缺 {', '.join(missing)}。自动安装被 JIULV_NO_INSTALL 关掉了。")
        return False

    upgrade_build_tools(py)
    if not pip_install(py, ".[parsers]"):
        say("依赖没装上。检查网络后重试；国内网络可以指定镜像：")
        say(f"  JIULV_PIP_INDEX={MIRROR} {which_python()} scripts/setup.py")
        return False

    still = missing_modules(py, REQUIRED)
    if still:
        say(f"装完仍然导入失败：{', '.join(still)}。跑一次诊断看细节：")
        say(f"  {py} -m jiulv.cli doctor")
        return False
    return True


def upstream_args(argv) -> list:
    """把 `--` 之后（或全部未知参数）当作要透传的东西。

    有 `--` 时它后面是子命令（`-- doctor`），否则默认是 start 的参数
    （`--port 8790`）。这样双击脚本不用关心命令行长什么样，原样丢进来即可。
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[idx + 1 :]
    return list(argv)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv
    rebuild = "--rebuild-env" in argv
    passthrough = [a for a in upstream_args(argv) if a not in ("--check", "--rebuild-env")]

    if not check_python():
        return 1

    if check_only:
        say(f"项目目录：{ROOT}")
        say(f"解释器：  {which_python()}（{sys.version.split()[0]}）")
        py = venv_python()
        if not py.exists():
            say("虚拟环境：还没建（首次启动会自动建）")
            return 0
        say(f"虚拟环境：{VENV_DIR}")
        if not venv_is_usable(py):
            say("          这个 .venv 不是在本目录建的（拷来的包会这样），下次启动会重建")
            return 1
        missing = missing_modules(py, REQUIRED)
        say("必需依赖：" + ("齐全" if not missing else "缺 " + ", ".join(missing)))
        opt = missing_modules(py, OPTIONAL)
        say("解析依赖：" + ("齐全" if not opt else "缺 " + ", ".join(opt) + "（docx/pdf/xlsx 会跳过）"))
        return 0 if not missing else 1

    py = ensure_venv(rebuild=rebuild)
    if not ensure_deps(py):
        return 1

    cmd = [str(py), "-m", "jiulv.cli"] + (passthrough or ["start"])
    if passthrough and passthrough[0].startswith("-"):
        cmd = [str(py), "-m", "jiulv.cli", "start"] + passthrough

    # 交给子进程后本进程就没用了。用 spawn 语义：Windows 上 execv 会丢掉
    # 控制台句柄，双击启动的窗口会立刻消失，报错也就看不见了。
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
