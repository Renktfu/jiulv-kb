"""注解与概念图测试。

这部分最容易悄悄坏掉：注解是正则从原文里抠的，图谱是规则拼的，
两者都不报错、只是结果变少。所以每个来源都单独验一遍数量级的合理性。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from jiulv import db
from jiulv.annotate import (
    annotations_from_sections,
    build_review_index,
    derive_signals,
    derive_title,
    match_review,
    parse_daily_reviews,
    run as run_annotations,
)
from jiulv.graph import build, concept_docs, dedup_key, doc_concepts, norm_hotel, subgraph
from jiulv.ingest import ingest
from jiulv.config import load_config

NOTE_S = """\
> 爆款等级：S级素材 | 编号：笔记200

### 笔记200：三亚保利瑰丽卫生堪忧

- **标题**：三亚保利瑰丽卫生堪忧
- **作者**：KiTiroll
- **互动数据**：320赞 / 480收藏 / 210评论
- **地区**：海南·三亚·保利瑰丽

#### 缺点/避坑点
- 地毯脚臭味严重，作者对味道敏感，一晚上无法入睡
- 房间清洁不到位：椰子盘不洗、口杯污垢厚
- 孩子大面积起疹子，疑似虫类叮咬

#### 优点
- 公区泳池和海景确实是海棠湾第一梯队

#### 提取要点
- 这家的问题集中在房间维护，不是硬件配置
- 写文案时：公区夸到顶，房间要有保留意见，别全夸
"""

NOTE_A = """\
### 笔记201：三亚保利瑰丽选房指南

- **标题**：三亚保利瑰丽选房指南
- **地区**：海南·三亚·保利瑰丽
- **互动数据**：180赞 / 260收藏 / 40评论

#### 房型分析
- 海景套房朝向决定能不能看到海，靠花园一侧只有一条缝

#### 预订启示
- 订之前先问清楚朝向，不接受指定房号的酒店要提前说
"""

DAILY = """\
# 每日采集记录 · 2026-10-28（周三）

## 采集清单

1. A_三亚_保利瑰丽选房指南（40 评论，180 赞 260 收藏，KiTiroll，nonblue）——把朝向讲成可核对的选择依据，不是泛泛而谈的海景。价值在房型对照表，可以直接抄结构。
"""

HOOK = """\
# 开头钩子 · 反常识型

## H-R-101
- 原文：海景房不一定看得到海。有的海景看的是隔壁楼和一条缝
- 类型：反常识
- 人群：C2
- 痛点：P2
- 机制：动摇默认假设。
- 转化：收藏
- 来源：自有
- 状态：待验证
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    write(root / "02_地区素材库" / "02_海南" / "S_三亚保利瑰丽卫生堪忧.md", NOTE_S)
    write(root / "02_地区素材库" / "02_海南" / "A_三亚保利瑰丽选房指南.md", NOTE_A)
    write(root / "06_每日采集产出" / "2026-10" / "2026-10-28_海南.md", DAILY)
    write(root / "13_钩子库" / "01_开头钩子" / "02_反常识型.md", HOOK)
    return root


@pytest.fixture()
def config(tmp_path: Path, corpus: Path):
    cfg = {
        "app": {"database": "kb.sqlite3"},
        "sources": [
            {
                "name": "测试库",
                "group": "test",
                "path": str(corpus),
                "include": ["**/*.md"],
                "exclude": [],
            }
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return load_config(path)


@pytest.fixture()
def conn(config):
    connection = db.open_db(config.db_path)
    ingest(config, connection, rebuild=True)
    yield connection
    connection.close()


# ---------------------------------------------------------------- 注解抽取


def test_sections_become_annotations():
    anns = annotations_from_sections(textwrap.dedent(NOTE_S))
    kinds = {a.kind for a in anns}
    assert "con" in kinds and "pro" in kinds and "takeaway" in kinds
    # 抬头字段不该被当成内容
    assert not any(a.text.startswith("标题") for a in anns)


def test_write_hint_is_promoted():
    anns = annotations_from_sections(textwrap.dedent(NOTE_S))
    write_hints = [a for a in anns if a.label == "写文案时"]
    assert len(write_hints) == 1
    assert write_hints[0].weight > 1.2
    # 前缀要去掉，只留结论
    assert not write_hints[0].text.startswith("写文案时")


def test_voice_annotations_are_capped():
    body = "#### 评论区咨询示例\n" + "\n".join(
        f"{i}. 这是第 {i} 条咨询内容，问房型怎么选" for i in range(1, 12)
    )
    anns = annotations_from_sections(body)
    assert len([a for a in anns if a.kind == "voice"]) <= 3


def test_daily_review_matching():
    items = parse_daily_reviews(textwrap.dedent(DAILY))
    assert len(items) == 1
    assert items[0]["grade"] == "A"
    index = build_review_index([textwrap.dedent(DAILY)])
    # 采集记录写的是文件名，笔记标题是另一套，两种都要能对上
    assert match_review(["A_三亚_保利瑰丽选房指南"], index) is not None
    assert match_review(["保利瑰丽选房指南"], index) is not None
    assert match_review(["完全不相关的另一篇笔记"], index) is None


def test_derived_signals_read_metrics():
    doc = {"metrics_json": '{"likes": 100, "saves": 420, "comments": 30}', "title": "三亚柏悦选房指南"}
    labels = [a.label for a in derive_signals(doc)]
    assert any("工具型" in label for label in labels)
    # 没有数据就不该硬编一条出来
    assert derive_signals({"metrics_json": "{}"}) == []


def test_title_patterns_flagged():
    anns = derive_title({"title": "三亚柏悦避雷！这间海景房看的是隔壁楼"})
    labels = " ".join(a.label for a in anns)
    assert "风险前置" in labels


# ---------------------------------------------------------------- 概念图


def test_hotel_name_normalised():
    assert norm_hotel("三亚保利瑰丽酒店（international，瑰丽集团）") == "三亚保利瑰丽"
    assert norm_hotel("腾冲石头纪（international，凯悦集团）") == "腾冲石头纪"
    # 一张帖子里列好几家时只取第一家，不然建出来是个假节点
    assert norm_hotel("三亚瑞吉；丽思；柏悦") == "三亚瑞吉"


def test_duplicate_titles_fold():
    a = dedup_key("在三亚保利瑰丽不太愉快的一天（有后续版）", "note", "u1")
    b = dedup_key("在三亚保利瑰丽不太愉快的一天（🈶后续版）", "note", "u2")
    assert a == b
    # 太短的标题不敢折叠，否则「避雷！！！」会吞掉一堆不同的笔记
    assert dedup_key("避雷！", "note", "u1") != dedup_key("避雷！", "note", "u2")


# ---------------------------------------------------------------- 端到端


def test_annotations_land_in_db(conn):
    total = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    assert total > 0
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM annotations")}
    assert {"con", "booking", "takeaway"} <= kinds
    # 采集点评要挂到具体笔记上
    assert conn.execute("SELECT COUNT(*) FROM annotations WHERE kind='review'").fetchone()[0] == 1


def test_concepts_and_links(conn):
    concepts = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    links = conn.execute("SELECT COUNT(*) FROM doc_concepts").fetchone()[0]
    assert concepts > 0 and links > 0
    # 两家笔记同酒店，应该合并成一个节点（酒店名从地区行里解析，不带城市前缀）
    row = conn.execute(
        "SELECT key, doc_count FROM concepts WHERE kind='hotel' AND key LIKE '%保利瑰丽%'"
    ).fetchone()
    assert row is not None and row["doc_count"] >= 1


def test_subgraph_and_backlinks(conn):
    hotel = conn.execute(
        "SELECT key FROM concepts WHERE kind='hotel' AND key LIKE '%保利瑰丽%' LIMIT 1"
    ).fetchone()["key"]
    data = subgraph(conn, f"hotel:{hotel}")
    assert data["focus"]["label"] == hotel
    assert len(data["nodes"]) >= 2
    assert any(n["kind"] == "doc" for n in data["nodes"])

    doc_id = conn.execute("SELECT id FROM docs WHERE title LIKE '%选房指南%'").fetchone()[0]
    assert doc_concepts(conn, doc_id)
    listed = concept_docs(conn, "region:海南", limit=10)
    assert listed["total"] >= 2


def test_annotations_rerun_is_idempotent(conn):
    first = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    run_annotations(conn)
    second = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    assert first == second

    links = conn.execute("SELECT COUNT(*) FROM doc_concepts").fetchone()[0]
    build(conn, None)
    assert conn.execute("SELECT COUNT(*) FROM doc_concepts").fetchone()[0] == links
