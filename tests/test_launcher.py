"""启动入口与自启的测试。

自启会碰系统状态（LaunchAgent / 计划任务），所以只测「算出来的东西对不对」：
端口探测、要写入的配置长什么样、dry-run 确实不落盘。真安装留给人工验证。
"""

from __future__ import annotations

import plistlib
import socket

import pytest
import yaml

from jiulv import launcher
from jiulv.config import load_config


@pytest.fixture()
def config(tmp_path):
    cfg = {"app": {"database": "kb.sqlite3"}, "sources": []}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return load_config(path)


def _listen() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def test_port_free_detects_listener():
    """有人在听的端口必须判为占用。

    这条是回归测试：最早用 bind + SO_REUSEADDR 判断，macOS 上对着一个已经在
    监听的端口 bind 竟然会成功，于是「被占」被当成「空闲」，最后 uvicorn 启动
    时才炸。
    """
    sock = _listen()
    try:
        port = sock.getsockname()[1]
        assert launcher.port_free("127.0.0.1", port) is False
    finally:
        sock.close()


def test_pick_port_skips_occupied():
    sock = _listen()
    try:
        port = sock.getsockname()[1]
        picked = launcher.pick_port("127.0.0.1", port)
        assert picked != port
        assert launcher.port_free("127.0.0.1", picked) is True
    finally:
        sock.close()


def test_running_instance_returns_none_on_dead_port():
    """空端口上不能误报「已经有一个 jiulv 在跑」，否则双击启动什么都不做。"""
    sock = _listen()
    port = sock.getsockname()[1]
    sock.close()
    assert launcher.running_instance("127.0.0.1", port) is None


def test_plist_is_valid_and_disables_browser(config):
    body = launcher._plist_body(config)
    data = plistlib.loads(body.encode("utf-8"))
    assert data["Label"] == launcher.LABEL
    assert data["RunAtLoad"] is True
    assert "--no-browser" in data["ProgramArguments"]
    # 自启必须带绝对路径的解释器，不能靠 PATH
    assert data["ProgramArguments"][0].endswith("python") or data["ProgramArguments"][0].endswith("python3")
    assert data["WorkingDirectory"] == str(config.base_dir)
    assert "logs" in data["StandardOutPath"]


def test_systemd_unit_uses_absolute_paths(config):
    body = launcher._systemd_body(config)
    assert "[Service]" in body
    assert f"WorkingDirectory={config.base_dir}" in body
    assert "--no-browser" in body
    assert "WantedBy=default.target" in body


def test_autostart_status_shape(config):
    info = launcher.autostart_status(config)
    for key in ("platform", "installable", "installed", "running", "target", "command", "log"):
        assert key in info
    assert info["command"].endswith("--no-browser")


def test_dry_run_writes_nothing(config):
    """dry-run 必须真的不落盘。装自启是不可见的状态修改，误触发很难发现。"""
    before = launcher.autostart_status(config)["installed"]
    info = launcher.autostart_install(config, dry_run=True)
    assert info["dry_run"] is True
    assert info["would_write"]
    assert launcher.autostart_status(config)["installed"] == before
