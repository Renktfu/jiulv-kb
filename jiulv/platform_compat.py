"""跨平台差异收在这一个文件里。

为什么单独成一个模块：Windows 上的坑不是一个，是一串，而且每个长得都不一样——
控制台编码、路径大小写、文件被占用、文件名非法、路径过长、目录联结成环。
散在十几个文件里排查时很难归因，集中之后就好办了：Windows 上出问题先看这里。

这个文件里的每个函数都只做一件事，且不 import 项目内其他模块，
这样它能在任何入口（CLI / MCP / 测试）里被最先导入，不会成环。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

# Windows 文件名里不允许出现的字符（含控制字符）
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

# Windows 保留设备名。CON.md、NUL.txt 一样打不开，扩展名救不了
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# 超过这个长度就要留意 Windows 的 260 字符上限（含盘符与文件名）
LONG_PATH_LIMIT = 240

# Windows 资源管理器与 Office 留下的垃圾，不该进库
OS_JUNK_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db"}
OS_JUNK_PREFIXES = ("~$", ".~")


# ------------------------------------------------------------------ 控制台


def setup_console() -> None:
    """把标准流统一成 utf-8。

    Windows 上 Python 直接写控制台是走 WriteConsoleW 的，中文没问题；但输出一旦被
    重定向（`jiulv search 三亚 > out.txt`、被别的程序当子进程调用、IDE 的终端面板），
    就退回按系统代码页编码：简体中文机器是 cp936，英文机器是 cp437。
    这时候打印「—」「·」「✅」这类字符会直接 UnicodeEncodeError 把命令搞崩。

    统一成 utf-8 + errors=replace：宁可掉一个字符，也不要让一条命令整个失败。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 被替换成 StringIO 之类的对象时跳过
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def read_stdio_lines() -> Iterator[str]:
    """按行读 stdin，按 utf-8 解码。

    MCP 走 stdio，客户端送来的 JSON 里有中文（`{"query": "三亚 亲子"}`）。
    Windows 上 sys.stdin 默认按代码页解码，UTF-8 的中文会被解成乱码，
    检索词错了还不报错——这种「静默错」比崩溃更难查。
    所以这里绕开文本层，直接读字节再自己解码。
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        for line in sys.stdin:  # 测试里塞了 StringIO 的情况
            yield line
        return
    for raw in buffer:
        yield raw.decode("utf-8", errors="replace")


def write_stdio_line(text: str) -> None:
    """往 stdout 写一行 JSON，只写 \\n。

    Windows 的文本模式会把 \\n 翻成 \\r\\n。严格按行解析的客户端会在 JSON 后面
    多看到一个 \\r，运气不好就解析失败。这里直接写字节，绕开翻译层。
    """
    payload = (text + "\n").encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
        return
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------ 路径


def path_key(path: Any) -> str:
    """路径的「同一性」键，用来判断两个写法是不是同一个目录。

    Windows 上路径大小写不敏感、两种斜杠都认：`C:\\Data\\KB`、`c:/data/kb/`
    是同一个目录。直接比字符串会把同一批文件当成两个来源收两遍，界面上还会
    出现「连了 14 个库、其中 3 个其实是同一个」这种怪事。

    normcase 在 Windows 上转小写并把 / 换成 \\，在 macOS/Linux 上原样返回，
    所以这个函数在哪都一样安全。
    """
    raw = os.path.expanduser(str(path or ""))
    if not raw:
        return ""
    try:
        raw = os.path.realpath(raw)
    except (OSError, ValueError):
        raw = os.path.abspath(raw)
    return os.path.normcase(raw)


def same_path(a: Any, b: Any) -> bool:
    return path_key(a) == path_key(b)


def path_too_long(path: Any) -> bool:
    """是否逼近 Windows 的 260 字符上限。非 Windows 永远返回 False。"""
    if not IS_WINDOWS:
        return False
    try:
        return len(os.path.abspath(str(path))) >= LONG_PATH_LIMIT
    except (OSError, ValueError):
        return False


def long_paths_enabled() -> Optional[bool]:
    """Windows 是否开了长路径支持。查不到返回 None，不当作失败。"""
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            return bool(value)
    except Exception:
        return None


def walk_files(root: Path) -> Iterator[Path]:
    """确定性遍历目录，不跟随符号链接/目录联结。

    `Path.rglob("*")` 在 3.12 之前会跟进目录联结（junction），Windows 上
    `AppData\\Local` 里就有成环的联结，一进去就是无限递归。os.walk 默认
    followlinks=False，且能按层排序，比 rglob 稳。
    """
    root = Path(root)
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            yield Path(dirpath) / name


def is_os_junk(name: str) -> bool:
    """系统/Office 留下的垃圾文件。`~$2026预算.xlsx` 是 Word/Excel 的锁文件，
    解析必然失败，还会在「解析失败」列表里刷屏。"""
    lowered = name.lower()
    if lowered in OS_JUNK_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in OS_JUNK_PREFIXES)


# ------------------------------------------------------------------ 写盘


def atomic_write_text(
    path: Any,
    text: str,
    encoding: str = "utf-8",
    newline: str = "\n",
    retries: int = 6,
) -> Path:
    """先写临时文件再 os.replace，中途失败不会留下半个文件。

    newline 固定成 \\n：同一份稿子在 Windows 和 macOS 上算出来的内容哈希要一致，
    否则在两边之间同步会触发「全部重新收录」。读的时候 Python 的通用换行会
    把 \\r\\n 归一成 \\n，所以只有写这一侧需要管。

    Windows 上 os.replace 可能因为目标正被别的进程（或杀毒软件）打开而抛
    PermissionError，这是常态不是异常，稍微退让重试几次。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with open(temp, "w", encoding=encoding, newline=newline) as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    last: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            os.replace(temp, target)
            return target
        except PermissionError as exc:
            last = exc
            time.sleep(0.06 * (attempt + 1))
        except OSError as exc:
            last = exc
            break
    try:
        temp.unlink()
    except OSError:
        pass
    if last:
        raise last
    return target


def safe_filename(
    text: str,
    limit: int = 60,
    fallback: str = "untitled",
    replacement: str = "-",
) -> str:
    """把任意标题变成在 Windows 上也合法的文件名。

    要处理的四件事：非法字符（`:` 最常见，标题里写「三亚：怎么选房」就中招）、
    保留设备名、结尾的点和空格（Windows 会静默截掉，导致写进去的文件名和
    以为的不一样）、长度。
    """
    cleaned = _ILLEGAL_CHARS.sub(replacement, (text or "").strip())
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"\s+", replacement, cleaned)
    cleaned = cleaned.strip(" ." + replacement)
    if not cleaned:
        return fallback
    stem = cleaned.split(".")[0].lower()
    if stem in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].strip(" .")
    return cleaned or fallback


# ------------------------------------------------------------------ 自检


def describe() -> Dict[str, Any]:
    """环境快照，给 `jiulv doctor` 用。只读，不改任何东西。"""
    import platform
    import sqlite3

    info: Dict[str, Any] = {
        "platform": sys.platform,
        "os_name": os.name,
        "is_windows": IS_WINDOWS,
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "fs_encoding": sys.getfilesystemencoding(),
        "stdout_encoding": (getattr(sys.stdout, "encoding", "") or ""),
        "sqlite": sqlite3.sqlite_version,
        "unicode_ok": _can_print_unicode(),
    }
    if IS_WINDOWS:
        info["platform_release"] = platform.release()
        info["long_paths_enabled"] = long_paths_enabled()
    return info


def _can_print_unicode() -> bool:
    """能不能把中文打出来。真打一次比看 encoding 名字准。"""
    encoding = getattr(sys.stdout, "encoding", "") or "ascii"
    try:
        "三亚艾迪逊".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def hints(info: Optional[Dict[str, Any]] = None) -> List[str]:
    """根据环境快照给出可执行建议。空列表表示没发现问题。"""
    info = info or describe()
    out: List[str] = []
    if not info.get("unicode_ok", True):
        out.append(
            "控制台编码不支持中文：在 Windows 上先执行 `chcp 65001`，"
            "或在命令前加 `set PYTHONUTF8=1`。"
        )
    if info.get("is_windows") and info.get("long_paths_enabled") is False:
        out.append(
            "Windows 未开启长路径支持。资料库路径很深时会报 OSError 206（路径过长）："
            "以管理员身份运行 `reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem "
            "/v LongPathsEnabled /t REG_DWORD /d 1 /f` 后重启。"
        )
    if info.get("is_windows") and sys.version_info < (3, 9):
        out.append("Python 版本偏低，本项目要求 3.9 以上。")
    return out
