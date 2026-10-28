"""跨平台层测试：文件名、原子写、路径同一性、遍历安全、stdio 编码、自检。

重点不是覆盖率，而是把「只在 Windows 上才炸」的那几件事钉成断言，
这样在 macOS/Linux 上改代码时也能守住——不用等搬去 Windows 才发现。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from jiulv import db, doctor, platform_compat as compat
from jiulv.config import load_config
from jiulv.ingest import ingest

# ------------------------------------------------------------------ 文件名


@pytest.mark.parametrize(
    "raw",
    [
        "三亚：怎么选房",          # 中文冒号没问题，但半角冒号会
        "三亚:怎么选房",
        "A/B 对比",
        "房型<升级>",
        '带娃"必看"',
        "什么|都不写",
    ],
)
def test_safe_filename_has_no_illegal_chars(raw):
    name = compat.safe_filename(raw)
    assert name
    for ch in '\\/:*?"<>|':
        assert ch not in name


def test_safe_filename_keeps_chinese_meaning():
    # 合法字符不该被吃掉：这个名字是要给人看的
    assert compat.safe_filename("三亚艾迪逊选房避坑") == "三亚艾迪逊选房避坑"
    assert compat.safe_filename("三亚 艾迪逊") == "三亚-艾迪逊"


def test_safe_filename_handles_reserved_device_names():
    """CON / NUL 在 Windows 上是设备名，带扩展名也打不开。"""
    assert compat.safe_filename("CON").startswith("_")
    assert compat.safe_filename("nul.txt").startswith("_")
    assert compat.safe_filename("COM3") == "_COM3"
    # 只是以 con 开头的不算
    assert compat.safe_filename("conrad 酒店") == "conrad-酒店"


def test_safe_filename_strips_trailing_dot_and_space():
    # Windows 会静默截掉结尾的点和空格，写进去的名字和以为的不一样
    assert compat.safe_filename("三亚选房...") == "三亚选房"
    assert compat.safe_filename("三亚选房 ") == "三亚选房"


def test_safe_filename_caps_length_and_falls_back():
    assert len(compat.safe_filename("三" * 200, limit=40)) == 40
    assert compat.safe_filename("   ") == "untitled"
    assert compat.safe_filename("///") == "untitled"


# ------------------------------------------------------------------ 原子写


def test_atomic_write_is_utf8_and_lf_only(tmp_path: Path):
    """固定的换行符：同一份稿子在 Windows 和 macOS 上算出的内容哈希要一致。"""
    target = tmp_path / "子目录" / "稿子.md"
    compat.atomic_write_text(target, "第一行\n第二行\n")

    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").startswith("第一行")


def test_atomic_write_overwrites_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "state.json"
    compat.atomic_write_text(target, '{"a": 1}')
    compat.atomic_write_text(target, '{"a": 2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ------------------------------------------------------------------ 路径


def test_path_key_treats_trailing_sep_and_dot_segments_as_same(tmp_path: Path):
    path = tmp_path / "资料库"
    path.mkdir()
    assert compat.path_key(str(path) + os.sep) == compat.path_key(path)
    assert compat.path_key(str(path / "." / ".." / path.name)) == compat.path_key(path)


def test_path_key_is_case_folded_only_on_windows(tmp_path: Path):
    path = tmp_path / "KB"
    path.mkdir()
    upper = compat.path_key(str(path))
    lower = compat.path_key(str(path).replace("KB", "kb"))
    if compat.IS_WINDOWS:
        assert upper == lower
    else:
        assert upper != lower  # macOS 上不折叠大小写，但 realpath 仍然会归一


def test_same_path_ignores_relative_noise(tmp_path: Path):
    target = tmp_path / "a"
    target.mkdir()
    assert compat.same_path(target, str(target) + "/./")
    assert not compat.same_path(target, tmp_path / "b")


# ------------------------------------------------------------------ 遍历


def test_is_os_junk():
    assert compat.is_os_junk("~$2026预算.xlsx")
    assert compat.is_os_junk("Thumbs.db")
    assert compat.is_os_junk("desktop.ini")
    assert not compat.is_os_junk("2026预算.xlsx")


def test_walk_files_does_not_follow_symlink_loops(tmp_path: Path):
    """Windows 上 AppData 里就有成环的目录联结，rglob 会无限递归。"""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "笔记.md").write_text("内容", encoding="utf-8")
    try:
        (root / "sub" / "loop").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许建目录符号链接")

    found = [p.name for p in compat.walk_files(root)]
    assert found.count("笔记.md") == 1
    assert len(found) < 10  # 跟着环走会爆掉


# ------------------------------------------------------------------ 收录


LOCK_AND_NOTE = {
    "02_地区素材库/笔记.md": "# 笔记\n- **标题**：三亚选房\n\n低楼层看不到海，带娃选高层双床。\n",
    "02_地区素材库/~$2026预算.docx": "office 锁文件，不是内容",
    "02_地区素材库/Thumbs.db": "缩略图缓存",
}


def test_ingest_skips_os_junk_files(tmp_path: Path):
    """Office 锁文件解析必然失败，收进来只会在失败列表里刷屏。"""
    root = tmp_path / "corpus"
    for rel, text in LOCK_AND_NOTE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "app": {"database": "kb.sqlite3"},
                "sources": [
                    {
                        "name": "测试",
                        "path": str(root),
                        "include": ["**/*.md", "**/*.docx"],
                        "exclude": [],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(cfg)
    conn = db.open_db(config.db_path)
    try:
        report = ingest(config, conn, rebuild=True)
        docs = conn.execute("SELECT rel_path FROM docs").fetchall()
    finally:
        conn.close()

    assert report.scanned == 1
    assert [row["rel_path"] for row in docs] == ["02_地区素材库/笔记.md"]


def test_prune_survives_path_case_change(tmp_path: Path):
    """同一批文件换了个大小写写法，不该被当成「全删了又新增」。"""
    root = tmp_path / "KB"
    root.mkdir()
    (root / "笔记.md").write_text(
        "# 三亚选房\n\n低楼层看不到海，带娃优先选高层双床，泳池在另一栋。\n",
        encoding="utf-8",
    )

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "app": {"database": "kb.sqlite3"},
                "sources": [{"name": "测试", "path": str(root), "include": ["**/*.md"]}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(cfg)
    conn = db.open_db(config.db_path)
    try:
        ingest(config, conn, rebuild=True)
        # 模拟另一台机器写进来的大小写不同的绝对路径
        row = conn.execute("SELECT id, abs_path FROM docs").fetchone()
        swapped = row["abs_path"].replace("/KB/", "/kb/")
        conn.execute("UPDATE docs SET abs_path=? WHERE id=?", (swapped, row["id"]))
        conn.commit()

        report = ingest(config, conn, rebuild=False)
        assert report.removed == 0
        assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 1
    finally:
        conn.close()


# ------------------------------------------------------------------ stdio


def test_write_stdio_line_uses_lf_and_utf8(monkeypatch):
    """MCP 写出去的每行都必须是 utf-8 + \\n，不能有 \\r。"""
    buffer = io.BytesIO()

    class FakeStdout:
        def __init__(self, raw):
            self.buffer = raw

        def write(self, text):  # pragma: no cover - 不该走到
            raise AssertionError("应该走 buffer")

        def flush(self):  # pragma: no cover
            pass

    monkeypatch.setattr(sys, "stdout", FakeStdout(buffer))
    compat.write_stdio_line('{"query": "三亚"}')

    payload = buffer.getvalue()
    assert payload.endswith(b"\n") and not payload.endswith(b"\r\n")
    assert json.loads(payload.decode("utf-8"))["query"] == "三亚"


def test_read_stdio_lines_decodes_utf8(monkeypatch):
    """按字节解码：Windows 上若按代码页解，中文会静默变乱码。"""
    raw = io.BytesIO('{"query": "三亚 亲子"}\n{"query": "瑞吉"}\n'.encode("utf-8"))

    class FakeStdin:
        buffer = raw

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    lines = [line.strip() for line in compat.read_stdio_lines()]
    assert json.loads(lines[0])["query"] == "三亚 亲子"
    assert json.loads(lines[1])["query"] == "瑞吉"


def test_mcp_server_roundtrip_over_real_pipe(tmp_path: Path):
    """真的起一个子进程，走管道收发中文。这条能抓到 stdio 层的编码回归。"""
    root = tmp_path / "corpus"
    (root).mkdir()
    (root / "笔记.md").write_text(
        "# 三亚艾迪逊选房\n- **标题**：三亚艾迪逊选房\n\n低楼层看不到海。\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "app": {"database": "kb.sqlite3"},
                "sources": [{"name": "测试", "path": str(root), "include": ["**/*.md"]}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(tmp_path / "config.yaml")
    conn = db.open_db(config.db_path)
    try:
        ingest(config, conn, rebuild=True)
    finally:
        conn.close()

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "kb_search", "arguments": {"query": "三亚"}},
        },
    ]
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "jiulv.cli", "mcp"],
        input=payload.encode("utf-8"),
        cwd=str(tmp_path),
        capture_output=True,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"\r\n" not in proc.stdout
    replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    assert replies[0]["result"]["serverInfo"]["name"] == "jiulv-kb"
    text = replies[1]["result"]["content"][0]["text"]
    assert "三亚" in text


# ------------------------------------------------------------------ 自检


def test_doctor_reports_sources(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "笔记.md").write_text("# 标题\n\n三亚选房。\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "app": {"database": "kb.sqlite3"},
                "sources": [{"name": "测试", "path": str(root), "include": ["**/*.md"]}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(cfg)
    data = doctor.collect(config)

    assert data["env"]["python"]
    assert data["db"]["exists"] is False
    assert data["source_items"][0]["docs"] == 1
    text = doctor.format_text(data)
    assert "索引库" in text and "来源" in text


def test_doctor_flags_missing_source(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "app": {"database": "kb.sqlite3"},
                "sources": [{"name": "断开的库", "path": str(tmp_path / "nope")}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    data = doctor.collect(load_config(cfg))
    assert data["source_items"][0]["exists"] is False
    assert "[断]" in doctor.format_text(data)
