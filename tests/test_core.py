"""核心链路测试：分词、抽取、收录、检索、组稿。

用一份临时的小语料跑，不依赖任何真实资料库，clone 下来就能测。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from jiulv import db
from jiulv.compose import compose
from jiulv.config import load_config
from jiulv.extract import chunk_text, excerpt_for, extract_meta, parse_metrics
from jiulv.ingest import ingest
from jiulv.search import search
from jiulv.tokenizer import query_tokens, tokenize

NOTE_A = """\
> 爆款等级：S级素材 | 编号：笔记100

### 笔记100：三亚文华东方最全选房指南
- **标题**：三亚文华东方最全选房指南
- **作者**：波波de酒店日记
- **互动数据**：839赞 / 1131收藏 / 33评论
- **地区**：海南·三亚·文华东方（大东海/珊瑚湾）

#### 房型序列
1. 翠园轩景观：园景，旁边有凤凰树
2. 观海轩景观：比翠园轩更完整、更宽阔的海平面

#### 评论区真实缺点
房间本身和服务太差劲了，虫子、起皮的墙、霉味的房间。公区无敌，房间选不对体验大打折扣。
"""

NOTE_B = """\
### 笔记101：带娃住三亚，亲子房是营销概念
- **标题**：带娃住三亚，别订亲子房
- **地区**：海南·三亚·仁恒皇冠
- **互动数据**：120赞 / 340收藏 / 210评论

亲子房是营销概念，你要的是双床。仁恒皇冠房间密集，等电梯久，灯光偏暗，
但儿童戏水区和早餐对带娃家庭算友好。海景房一定要先确定朝向。
"""

RULE = """\
# 瑞克克风格文案写作规则总库

## 二、强制性规则

每家酒店不能全夸，也不能全骂，要有具体的好和具体的坏。

凡是真实资料里没有的内容一律不写，宁可不写也不编。
剔除价格，不写具体价格数字，但可以用千元档这类相对描述。
"""

HOOK = """\
# 开头钩子 · 反常识型

## H-R-001
- 原文：带娃别订亲子房。亲子房是营销概念，你要的是双床
- 类型：反常识
- 人群：M1 M3
- 痛点：P3 + P4
- 机制：否掉所有人都在推荐的选项，再给更具体的替代。
- 转化：收藏 + 评论
- 来源：自有
- 状态：待验证
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    write(root / "02_地区素材库" / "02_海南" / "S_100_三亚文华东方选房指南.md", NOTE_A)
    write(root / "02_地区素材库" / "02_海南" / "A_101_带娃住三亚.md", NOTE_B)
    write(root / "04_风格规则与范文" / "01_规则总库" / "写作规则总库.md", RULE)
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


# ------------------------------------------------------------------ 分词


def test_cjk_bigram_tokenizer():
    tokens = tokenize("三亚文华东方")
    assert "三亚" in tokens
    assert "文华" in tokens
    # 没有单字噪音，长度都是 2
    assert all(len(t) == 2 for t in tokens)


def test_query_tokens_drop_stopwords():
    tokens = query_tokens("三亚的酒店怎么样")
    assert "三亚" in tokens
    assert "的" not in tokens


# ------------------------------------------------------------------ 抽取


def test_parse_metrics_variants():
    assert parse_metrics("839赞 / 1131收藏 / 33评论")["saves"] == 1131
    assert parse_metrics("1.2万赞")["likes"] == 12000
    assert parse_metrics("146 评论，13 赞 66 收藏")["comments"] == 146


def test_extract_meta_from_note():
    meta = extract_meta(NOTE_A, "02_地区素材库/02_海南/S_100_x.md", "S_100_x")
    assert meta.title == "三亚文华东方最全选房指南"
    assert meta.doc_type == "note"
    assert meta.grade == "S"
    assert meta.region == "海南"
    assert meta.hotel == "文华东方"
    assert meta.author == "波波de酒店日记"
    assert meta.metrics["comments"] == 33


def test_chunk_text_splits_by_heading():
    chunks = chunk_text(NOTE_A)
    assert len(chunks) >= 1
    assert any("房型" in heading or "房型" in body for heading, body in chunks)


def test_excerpt_skips_field_lines():
    excerpt = excerpt_for(NOTE_A, ["房型"], 200)
    assert "标题" not in excerpt
    assert "翠园轩" in excerpt or "房型" in excerpt


# ------------------------------------------------------------------ 收录与检索


def test_ingest_counts(conn):
    stats = db.stats(conn)
    assert stats["docs"] == 4
    assert stats["hooks"] == 1
    assert stats["by_type"]["note"] == 2
    assert stats["by_type"]["rule"] == 1
    assert stats["by_type"]["hook"] == 1


def test_search_finds_two_char_query(conn, config):
    result = search(conn, "三亚", config=config, limit=5)
    assert result["total"] >= 2
    titles = [hit["doc"]["title"] for hit in result["hits"]]
    assert any("文华东方" in title or "带娃" in title for title in titles)


def test_search_filters_by_region(conn, config):
    result = search(conn, "选房", config=config, limit=5, region="长白山")
    assert result["total"] == 0


def test_search_snippet_has_marks(conn, config):
    result = search(conn, "亲子房", config=config, limit=5)
    assert result["hits"]
    assert "«" in result["hits"][0]["snippet"]


# ------------------------------------------------------------------ 组稿


def test_compose_builds_prompt(conn, config):
    pack = compose(conn, config, {"hotel": "文华东方", "region": "海南", "persona": "带娃", "pain": "选房"})
    assert "文华东方" in pack["prompt"]
    assert "不写具体价格数字" in pack["prompt"]
    assert pack["hooks"]
    assert pack["references"]
    assert pack["brief"]["audience"] == "M1"
    assert "只在" in pack["prompt"] or "只准用这些资料" in pack["prompt"]


def test_compose_warns_when_hotel_missing(conn, config):
    pack = compose(conn, config, {"hotel": "不存在的酒店", "region": "海南"})
    assert any("不存在的酒店" in warning for warning in pack["warnings"])
