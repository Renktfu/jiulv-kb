"""启动入口：一条命令把整个库跑起来，以及开机自启的安装/卸载。

为什么要单独一个模块：`serve` 只管接口，`watch` 只管同步，两者要各开一个终端。
日常用的人不会记得开两个窗口，也不该记。`jiulv start` 把三件事收成一件：

    第一次跑 → 先把库建起来（没库或没来源时自动补）
    常驻      → 网页服务 + 后台同步线程，一个进程，关窗口就全停
    省事      → 起来之后自己开浏览器，端口被占就自己换一个

自启（`jiulv autostart`）只管「登入时自动执行 jiulv start --no-browser」这一件事，
每个平台各用各的官方机制：macOS 用 LaunchAgent，Linux 用 systemd --user，
Windows 用计划任务。都不需要管理员权限，都能一条命令卸掉。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .platform_compat import IS_MACOS, IS_WINDOWS, atomic_write_text

LABEL = "cn.jiulv.kb"
TASK_NAME = "jiulv-kb"


# ------------------------------------------------------------------ 端口


def port_free(host: str, port: int) -> bool:
    """端口空不空。只探测，不占用。

    不能只靠 bind 判断：macOS 上带着 SO_REUSEADDR 去 bind 一个已经有人在监听的
    端口，居然会成功——于是「被占用」被误判成「空闲」，最后是 uvicorn 启动时才
    炸出 address already in use。所以先用 connect 探一下有没有人在听，没有再试 bind
    （这一步挡的是 TIME_WAIT 之类的残留状态）。
    """
    probe = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        if sock.connect_ex((probe, port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def running_instance(host: str, port: int) -> Optional[Dict[str, Any]]:
    """端口上是不是已经有一个 jiulv 在跑。

    分两种情况：端口被占但服务是自己的（重复启动，直接开浏览器就行），
    端口被别的程序占（换端口）。不区分的话，重复双击启动会得到一个
    「address already in use」堆栈，用户以为坏了。
    """
    url = f"http://{host}:{port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=1.2) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if isinstance(data, dict) and "docs" in data and "version" in data:
        return data
    return None


def pick_port(host: str, port: int, tries: int = 12) -> int:
    """从配置的端口往后找第一个空的。占用的多半是上次没关掉的自己。"""
    for offset in range(tries):
        candidate = port + offset
        if port_free(host, candidate):
            return candidate
    return port


def _url(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}/"


def _open_later(url: str, delay: float = 1.6) -> None:
    """延后开浏览器：uvicorn.run 会阻塞，直接用线程等一会儿再开。
    不等的话浏览器先到一步，会看到「无法连接」，得手动刷新。"""
    def run() -> None:
        time.sleep(delay)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ start


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _bootstrap(config: Config) -> None:
    """第一次启动时把库补齐：没来源就探测，没库就建。

    这两步只在必要时跑。有库有来源的人每次启动不该等一次全量扫描。
    """
    if not config.sources:
        from . import connectors

        result = connectors.connect(config)
        config.reload()
        if config.sources:
            _log(
                f"探测到 {len(config.sources)} 个资料库，"
                f"约 {result.get('docs_added', 0)} 篇，已写进 sources.yaml"
            )
        else:
            _log("没有配置任何来源，先跑 jiulv connect 或手写 config.yaml 的 sources")

    if not config.db_path.exists():
        if not config.sources:
            return
        from . import sync as sync_mod

        _log("还没有索引，先建一次（1500 篇约半分钟）")
        result = sync_mod.sync_once(config, force=True, logger=lambda m: _log(m))
        report = result.get("report") or {}
        _log(
            f"建好了：{report.get('added', 0)} 篇，"
            f"注解 {report.get('annotations', 0)} 条，{result.get('duration_s')}s"
        )


def start(
    config: Config,
    host: Optional[str] = None,
    port: Optional[int] = None,
    interval: Optional[int] = None,
    browser: bool = True,
    watch_enabled: bool = True,
    log: bool = True,
) -> int:
    import uvicorn

    from .api import create_app

    say = _log if log else (lambda msg: None)
    host = host or config.get("app", "host", default="127.0.0.1")
    wanted = int(port or config.get("app", "port", default=8787))

    existing = running_instance(host, wanted)
    if existing:
        url = _url(host, wanted)
        say(f"已经在运行了（{existing.get('docs')} 篇，版本 {existing.get('version')}）：{url}")
        if browser:
            _open_later(url, delay=0.1)
        return 0

    port = pick_port(host, wanted)
    if port != wanted:
        say(f"端口 {wanted} 被别的程序占着，改用 {port}")

    _bootstrap(config)

    if watch_enabled:
        from . import sync as sync_mod

        every = int(interval or config.get("connect", "watch_interval", default=120))
        threading.Thread(
            target=sync_mod.watch,
            kwargs={"config": config, "interval": every, "logger": say},
            daemon=True,
            name="jiulv-watch",
        ).start()
    else:
        say("后台同步已关（--no-watch），资料库的变化要手动 jiulv sync")

    url = _url(host, port)
    say(f"网页界面： {url}")
    say(f"接口文档： {url}docs")
    if browser:
        _open_later(url)

    uvicorn.run(create_app(config.path), host=host, port=port, log_level="warning")
    return 0


# ------------------------------------------------------------------ 自启


def _entry_command() -> List[str]:
    """自启时执行的命令。用当前解释器的绝对路径，不依赖 PATH。

    带 --no-browser：开机自动开一堆浏览器标签是骚扰，不是便利。
    """
    return [sys.executable, "-m", "jiulv.cli", "start", "--no-browser"]


def _log_file(config: Config) -> Path:
    return config.db_path.parent / "logs" / "jiulv.log"


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _macos_plist(config: Config) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_unit(config: Config) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{TASK_NAME}.service"


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _plist_body(config: Config) -> str:
    args = "".join(f"        <string>{_xml_escape(a)}</string>\n" for a in _entry_command())
    log = _xml_escape(_log_file(config))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>WorkingDirectory</key>
    <string>{_xml_escape(config.base_dir)}</string>
    <key>RunAtLoad</key>
    <true/>
    <!-- KeepAlive 用 SuccessfulExit=false：崩了自动拉起来，自己退出的不纠缠，
         否则 `jiulv autostart off` 之后 launchd 还会立刻重启一次。 -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _systemd_body(config: Config) -> str:
    return f"""[Unit]
Description=jiulv-kb 酒旅爆款知识库
After=network.target

[Service]
Type=simple
WorkingDirectory={config.base_dir}
ExecStart={" ".join(_entry_command())}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _windows_task_command() -> str:
    """schtasks /TR 要一条完整命令行，含空格的路径必须自己带引号。"""
    return " ".join(f'"{a}"' if " " in a else a for a in _entry_command())


def autostart_status(config: Config) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "platform": "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux"),
        "installable": True,
        "installed": False,
        "running": False,
        "command": " ".join(_entry_command()),
        "log": str(_log_file(config)),
        "detail": "",
    }
    if IS_WINDOWS:
        info["target"] = f"计划任务 {TASK_NAME}"
        result = _run(["schtasks", "/Query", "/TN", TASK_NAME])
        info["installed"] = result.returncode == 0
        info["running"] = info["installed"]  # 计划任务没有「在跑」这个概念，只能看有没有登记
    elif IS_MACOS:
        info["target"] = str(_macos_plist(config))
        info["installed"] = _macos_plist(config).exists()
        result = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        info["running"] = result.returncode == 0
        if info["running"]:
            info["detail"] = "launchd 已加载"
    else:
        info["target"] = str(_systemd_unit(config))
        info["installed"] = _systemd_unit(config).exists()
        if shutil.which("systemctl"):
            result = _run(["systemctl", "--user", "is-active", TASK_NAME])
            info["running"] = result.stdout.strip() == "active"
        else:
            info["installable"] = False
            info["detail"] = "没有 systemctl，这个发行版请用 cron 或自建 unit"
    return info


def _launchd_loaded() -> bool:
    """launchd 里到底有没有这个 job。

    不能用命令返回码判断：`launchctl load -w` 在新版 macOS 上早已废弃，
    失败时也可能返回 0，看着「成功」其实什么都没加载。
    唯一可信的是 `launchctl print` 能不能找到它。
    """
    result = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    return result.returncode == 0


def autostart_install(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """装上自启。只写用户级配置，不动系统目录，不需要 sudo。"""
    status = autostart_status(config)
    status["dry_run"] = dry_run

    if IS_WINDOWS:
        if dry_run:
            status["would_write"] = _windows_task_command()
            return status
        result = _run([
            "schtasks", "/Create", "/F",
            "/SC", "ONLOGON",
            "/TN", TASK_NAME,
            "/TR", _windows_task_command(),
        ])
        status["ok"] = result.returncode == 0
        status["message"] = (result.stdout or result.stderr).strip()
        return status

    if IS_MACOS:
        path, body = _macos_plist(config), _plist_body(config)
    else:
        path, body = _systemd_unit(config), _systemd_body(config)

    if dry_run:
        status["would_write"] = str(path)
        status["would_body"] = body
        return status

    _log_file(config).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, body)

    # 写入就算装好了：macOS 登录时会自动加载 ~/Library/LaunchAgents 里的 plist，
    # systemd 用户服务同理，enable 只是让它「现在」也起来。
    # 单独记住「立刻加载成功没有」——在没有图形会话的终端里（SSH、CI、某些
    # 远程 shell）bootstrap 会返回 EIO，但文件已经就位，注销重登照样生效。
    # 把这种情况报成失败会让人白折腾。
    status["installed"] = True
    status["ok"] = True

    if IS_MACOS:
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        result = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
        if not _launchd_loaded():
            _run(["launchctl", "load", "-w", str(path)])
        status["loaded"] = _launchd_loaded()
        if status["loaded"]:
            status["message"] = "已加载，现在就跑着，以后每次登录自动启动"
        else:
            raw = (result.stderr or result.stdout).strip().splitlines()
            reason = raw[0][:70] if raw else ""
            status["message"] = (
                "已写入，下次登录自动启动"
                + (f"。当前会话里立刻加载失败（{reason}），" if reason else "。")
                + "这个终端多半不在图形会话里，注销重登即可；想现在就用请双击 scripts/jiulv-start.command"
            )
    else:
        _run(["systemctl", "--user", "daemon-reload"])
        result = _run(["systemctl", "--user", "enable", "--now", TASK_NAME])
        status["loaded"] = result.returncode == 0
        status["message"] = (
            "已启用并启动"
            if status["loaded"]
            else "已写入，下次登录自动启动（当前会话没有 systemd 用户总线，所以没能立刻启用）"
        )

    return status


def autostart_remove(config: Config) -> Dict[str, Any]:
    status = autostart_status(config)
    if IS_WINDOWS:
        result = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        status["ok"] = result.returncode == 0
        status["message"] = (result.stdout or result.stderr).strip()
    elif IS_MACOS:
        path = _macos_plist(config)
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        status["ok"] = not path.exists()
        status["message"] = "已卸载 LaunchAgent" if status["ok"] else "卸载失败，手动删 " + str(path)
    else:
        _run(["systemctl", "--user", "disable", "--now", TASK_NAME])
        try:
            _systemd_unit(config).unlink()
        except FileNotFoundError:
            pass
        _run(["systemctl", "--user", "daemon-reload"])
        status["ok"] = not _systemd_unit(config).exists()
        status["message"] = "已卸载 systemd 用户服务"
    status["installed"] = False
    status["running"] = False
    return status
