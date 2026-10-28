"""增量迭代相关：新资料进来时会不会被漏掉、会不会把原有来源冲掉。

这几条对应真实踩到的坑，不是假想：

1. 带 `--only` 连一个新来源，`sources.yaml` 被整份覆盖成只剩那一条，
   其余十几个来源静默消失。下一次同步它们就被当成"来源已删除"。
2. 归档笔记的互动数据写成「观看：4501　点赞：103」，旧解析器把 4501
   当成点赞、103 当成收藏，整篇数据错位一层。
3. 没有人工分级的笔记等级为空，按等级排序时永远沉在最后。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from jiulv import connectors, sync
from jiulv.config import load_config
from jiulv.extract import extract_meta, parse_metrics

ARCHIVE_NOTE = """\
# 圈内解答:三亚索菲特房型及房号的秘密

- 发布时间：2025-01-04 12:22
- 观看：4501　点赞：103　收藏：177　评论：62
- 笔记ID：6778b792000000000800f37a
- 图片数：2

---

索菲特的海景房分东西两翼，东翼看海，西翼看园林。
"""

INDEX_PAGE = """\
# 小红书笔记归档 · 2026-10-28

- 全量笔记：118 篇｜正文 117 篇
- 总观看 563,626｜总点赞 8,455｜总收藏 10,114
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch) -> dict:
    wb = tmp_path / "WorkBuddy"
    oc = tmp_path / ".openclaw"
    write(wb / "2026-10-16-16-08-58" / "小红书酒旅资料库" / "02_地区素材库" / "A.md", "# 旧笔记\n\n内容。\n")
    write(oc / "workspace" / "reference" / "房型.md", "# 房型\n\n内容。\n")
    write(
        oc / "workspace" / "projects" / "xhs-archive" / "正文"
        / "103赞-圈内解答_三亚索菲特房型及房号的秘密.md",
        ARCHIVE_NOTE,
    )
    write(oc / "workspace" / "projects" / "xhs-archive" / "INDEX.md", INDEX_PAGE)
    monkeypatch.setenv("JIULV_WB_ROOT", str(wb))
    monkeypatch.setenv("JIULV_OC_ROOT", str(oc))
    return {"wb": wb, "oc": oc, "tmp": tmp_path}


@pytest.fixture()
def config(roots, tmp_path: Path):
    cfg = {"app": {"database": "kb.sqlite3"}, "sources": []}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return load_config(path)


# ------------------------------------------------------------------ 来源探测


def test_archive_profile_is_connected_by_default(config):
    """笔记归档不能挂在 --deep 后面，否则新笔记永远进不来。"""
    found = {c.profile: c for c in connectors.discover(config)}
    assert "oc-archive" in found
    assert found["oc-archive"].docs == 2


def test_connect_twice_keeps_earlier_sources(config):
    """第二次 connect 不能把第一次写进去的来源清掉。"""
    connectors.connect(config)
    first = {s["name"] for s in yaml.safe_load((config.base_dir / "sources.yaml").read_text("utf-8"))["sources"]}
    assert len(first) >= 3

    connectors.connect(config)
    second = {s["name"] for s in yaml.safe_load((config.base_dir / "sources.yaml").read_text("utf-8"))["sources"]}
    assert second == first


def test_connect_only_does_not_drop_other_sources(config):
    """`--only` 只限定这次连哪一类，不该顺手删掉别的来源。"""
    connectors.connect(config)
    before = yaml.safe_load((config.base_dir / "sources.yaml").read_text("utf-8"))["sources"]
    assert len(before) >= 3

    connectors.connect(config, only=["oc-archive"])
    after = yaml.safe_load((config.base_dir / "sources.yaml").read_text("utf-8"))["sources"]
    assert len(after) == len(before)
    assert {s["path"] for s in after} == {s["path"] for s in before}


# ------------------------------------------------------------------ 归档笔记解析


def test_parse_metrics_leading_unit():
    """单位在前：观看：4501　点赞：103　收藏：177　评论：62"""
    got = parse_metrics("观看：4501　点赞：103　收藏：177　评论：62")
    assert got == {"views": 4501, "likes": 103, "saves": 177, "comments": 62}


def test_parse_metrics_does_not_eat_date():
    """发布时间里的 16 不能变成点赞数。"""
    got = parse_metrics("- 发布时间：2026-06-16　点赞：896　笔记ID：6a32e1c4")
    assert got["likes"] == 896


def test_parse_metrics_thousands_separator():
    got = parse_metrics("总观看 563,626｜总点赞 8,455")
    assert got["views"] == 563626 and got["likes"] == 8455


def test_parse_metrics_suffix_form_still_works():
    """后缀写法不能被新规则带偏：146 条评论、13 个赞、66 个收藏。"""
    got = parse_metrics("146 评论，13 赞 66 收藏")
    assert got == {"comments": 146, "likes": 13, "saves": 66}


def test_archive_note_meta():
    meta = extract_meta(ARCHIVE_NOTE, "正文/103赞-x.md", "103赞-x", "OpenCLaw 笔记归档")
    assert meta.title == "圈内解答:三亚索菲特房型及房号的秘密"
    assert meta.doc_type == "note"
    assert meta.grade == "A"  # 103 赞落进 A 档
    assert meta.published_at == "2025-01-04 12:22"
    assert meta.metrics["likes"] == 103 and meta.metrics["views"] == 4501


def test_grade_falls_back_to_likes():
    meta = extract_meta(ARCHIVE_NOTE, "正文/x.md", "x", "OpenCLaw 笔记归档")
    assert meta.grade == "A"
    low = extract_meta("# 只有 1 个赞\n\n- 点赞：1\n", "正文/y.md", "y", "笔记归档")
    assert low.grade == ""


def test_index_page_gets_no_grade():
    """索引页罗列全库数据，按点赞推等级会推出一个假的 S 级。"""
    meta = extract_meta(INDEX_PAGE, "INDEX.md", "INDEX", "OpenCLaw 笔记归档")
    assert meta.doc_type == "index"
    assert meta.grade == ""


def test_human_grade_wins_over_likes():
    text = "# 标题\n\n> 爆款等级：B级素材\n\n- 点赞：9999\n\n正文。\n"
    meta = extract_meta(text, "02_地区素材库/x.md", "x")
    assert meta.grade == "B"


# ------------------------------------------------------------------ 常驻监听


def test_watch_picks_up_new_source_without_restart(config, roots):
    """常驻监听每轮重读配置：新接的来源不用重启就能被扫到。"""
    connectors.connect(config)
    config.reload()  # connect 写完盘，内存里的 config 还是旧的
    before = len(config.sources)

    # 模拟用户在服务运行期间又接了一个目录
    extra = roots["oc"] / "workspace" / "cases"
    write(extra / "新经验.md", "# 新经验\n\n内容。\n")
    payload = yaml.safe_load((config.base_dir / "sources.yaml").read_text("utf-8"))
    payload["sources"].append(
        {
            "name": "后加的来源",
            "group": "openclaw",
            "path": str(extra),
            "include": ["**/*.md"],
            "exclude": [],
            "enabled": True,
        }
    )
    (config.base_dir / "sources.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )

    logs = []
    sync.watch(config, max_rounds=1, logger=logs.append)

    assert len(config.sources) == before + 1
    assert any("后加的来源" in line for line in logs)
    assert any("配置变了" in line for line in logs)


def test_watch_survives_broken_yaml(config):
    """手改坏 YAML 不能让监听线程崩掉。"""
    connectors.connect(config)
    (config.base_dir / "sources.yaml").write_text("sources: [坏掉的 yaml", encoding="utf-8")

    logs = []
    sync.watch(config, max_rounds=1, logger=logs.append)
    assert any("重读配置失败" in line for line in logs)
