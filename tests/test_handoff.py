"""把包交到别人电脑上之后，库还能不能跟着他的产出长。

这一组守的是「交接」这条路径，和本机日常迭代是两个场景：

1. 一个来源都没接的时候，`jiulv sync` 必须明说没接目录。空库回一句
   「无变化」最误导人——人会以为同步成功了，然后对着空白页面找原因。
2. 分发包里的 config.example.yaml 不能带作者自己的绝对路径。带了的话，
   别人 init 出来就是一串「跳过来源：目录不存在」，还以为自己配错了。
3. 新笔记丢进已接过的目录、改过、删掉，三种都要真的反映到库里。
   这是「资料库更新」的全部语义，需要一条端到端的证据。
"""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import yaml

from jiulv import cli, sync
from jiulv.config import load_config

PKG_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def make_config(tmp_path: Path, sources: list) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"app": {"database": "data/kb.sqlite3"}, "sources": sources},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return cfg


# ------------------------------------------------------------------ 没接目录


def test_sync_without_sources_says_not_connected(tmp_path: Path, capsys):
    """空来源时不能回「无变化」，要直接说没接目录并给两条路。"""
    cfg = make_config(tmp_path, [])
    args = argparse.Namespace(config=str(cfg), force=False, source=None, quiet=True, json=False)
    code = cli.cmd_sync(args)
    out = capsys.readouterr().out
    assert code == 1
    assert "还没有接任何资料目录" in out
    assert "connect" in out and "init" in out


def test_sync_without_sources_does_not_create_index(tmp_path: Path):
    """连目录都没接，不该顺手建一个空库出来。"""
    cfg = make_config(tmp_path, [])
    args = argparse.Namespace(config=str(cfg), force=False, source=None, quiet=True, json=False)
    cli.cmd_sync(args)
    assert not (tmp_path / "data" / "kb.sqlite3").exists()


# ------------------------------------------------------------------ 示例配置


def test_example_config_has_no_author_paths():
    """示例配置会随包发给别人，生效的那部分不能出现作者的路径。

    以前这里写着作者本机的 ~/WorkBuddy/<时间戳>/… 和 ~/.openclaw/…，
    别人一 init，日志里就是两条「跳过来源：目录不存在」。
    注释里的示例路径是给人照着改的，允许保留。
    """
    text = (PKG_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    active = "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    for marker in ("/Users/", "C:\\Users", "2026-10-16", "~/.openclaw", "workbuddy/"):
        assert marker not in active, f"示例配置生效部分残留了作者路径：{marker}"


def test_example_config_keeps_library_source_active():
    """自带资料库那条要默认生效，否则新包打开是彻底空的。"""
    data = yaml.safe_load((PKG_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    active = data["sources"]
    assert len(active) == 1
    assert active[0]["group"] == "library"
    assert active[0]["path"] == "data/library"


# ------------------------------------------------------------------ 端到端更新


def test_new_edited_deleted_files_follow_into_index(tmp_path: Path):
    """新增 / 改写 / 删除三种动作都要落到库里，这是资料库更新的全部。"""
    notes = tmp_path / "我的笔记"
    write(notes / "太舞.md", "# 太舞实测\n\n缆车排队八分钟，雪道够宽，初级道适合新手。\n")
    cfg = make_config(
        tmp_path,
        [{"name": "我的笔记", "group": "mine", "path": str(notes), "include": ["**/*.md"]}],
    )
    config = load_config(cfg)

    first = sync.sync_once(config)
    assert first["report"]["added"] == 1

    # 同一个文件改内容：篇数不变，但要重新解析
    write(notes / "太舞.md", "# 太舞实测\n\n缆车排队八分钟，雪道够宽。周日下午人少一半。\n")
    second = sync.sync_once(config)
    assert second["report"]["updated"] == 1
    assert second["report"]["added"] == 0

    # 新加一篇
    write(notes / "万龙.md", "# 万龙雪场\n\n风大，缆车快，中级道比太舞长一截。\n")
    third = sync.sync_once(config)
    assert third["report"]["added"] == 1

    # 删掉一篇
    (notes / "太舞.md").unlink()
    fourth = sync.sync_once(config)
    assert fourth["report"]["removed"] == 1


def test_short_file_is_reported_not_silently_swallowed(tmp_path: Path):
    """正文不到 20 字不收可以，但要报出来。

    「崇礼万龙 风大缆车快」这种一句话笔记会被长度门槛拦掉。以前直接
    计入 skipped，命令行只说「已同步 +0 篇」，看着像同步成功，实际没进。
    """
    notes = tmp_path / "笔记"
    write(notes / "短.md", "# 万龙\n\n风大，缆车快。\n")
    write(notes / "长.md", "# 万龙雪场两日实测\n\n缆车排队八分钟，中级道比太舞长，风大要带面罩。\n")
    cfg = make_config(
        tmp_path,
        [{"name": "笔记", "group": "mine", "path": str(notes), "include": ["**/*.md"]}],
    )
    result = sync.sync_once(load_config(cfg))
    assert result["report"]["added"] == 1
    assert result["report"]["short"] == 1
    assert result["report"]["short_files"][0]["path"] == "短.md"


def test_nothing_changed_costs_one_skip(tmp_path: Path):
    """没变化时整轮跳过，不重复解析。后台每两分钟一轮，不能每次都干活。"""
    notes = tmp_path / "笔记"
    write(notes / "A.md", "# A\n\n内容。\n")
    cfg = make_config(
        tmp_path,
        [{"name": "笔记", "group": "mine", "path": str(notes), "include": ["**/*.md"]}],
    )
    config = load_config(cfg)
    sync.sync_once(config)
    again = sync.sync_once(config)
    assert again.get("skipped") is True
