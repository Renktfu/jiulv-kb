"""列表接口的去重：同一篇内容被几个目录各收一份时，列表里只留一份。

真实资料库里这种情况很普遍（实测 1888 篇里只有 927 篇是不同内容），
不去重的话点一个地区，满屏都是同一篇的副本。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from jiulv import db
from jiulv.config import load_config
from jiulv.ingest import ingest
from jiulv.search import count_unique_docs, facets, list_docs

SAME = """\
### 笔记200：三亚柏悦黄金房号
- **标题**：三亚柏悦黄金房号
- **地区**：海南·三亚·柏悦
- **互动数据**：300赞 / 500收藏 / 20评论

海景房要挑 08 号，正对海面；12 号正对别栋，别选。
"""

OTHER = """\
### 笔记201：长白山雪季住哪
- **标题**：长白山雪季别住山门外
- **地区**：长白山·万达度假区
- **互动数据**：88赞 / 210收藏 / 12评论

雪季住山门外每天要倒两趟车，带娃的基本撑不住，宁可加钱住度假区内。
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture()
def conn(tmp_path: Path):
    root = tmp_path / "corpus"
    # 同一篇内容放进两个目录，模拟被两个来源各收一份
    write(root / "A" / "笔记200.md", SAME)
    write(root / "B" / "备份" / "笔记200.md", SAME)
    write(root / "A" / "笔记201.md", OTHER)

    cfg = {
        "app": {"database": "kb.sqlite3"},
        "sources": [
            {
                "name": "测试库",
                "group": "test",
                "path": str(root),
                "include": ["**/*.md"],
                "exclude": [],
            }
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    config = load_config(path)
    connection = db.open_db(config.db_path)
    ingest(config, connection, rebuild=True)
    yield connection
    connection.close()


def test_ingest_actually_stored_the_copy(conn):
    """先确认库里真有 3 条，否则去重测试是假的。"""
    assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 3


def test_dedupe_collapses_same_content(conn):
    got = list_docs(conn, limit=50)
    assert got["total"] == 2, "两条不同内容，副本不该算进总数"
    titles = sorted(item["title"] for item in got["items"])
    assert titles == ["三亚柏悦黄金房号", "长白山雪季别住山门外"]
    collapsed = [item for item in got["items"] if item["duplicates"]]
    assert len(collapsed) == 1
    assert collapsed[0]["duplicates"] == 1


def test_dedupe_off_keeps_every_copy(conn):
    got = list_docs(conn, limit=50, dedupe=False)
    assert got["total"] == 3
    assert len(got["items"]) == 3


def test_pagination_uses_deduped_total(conn):
    first = list_docs(conn, limit=1, offset=0, order="grade")
    second = list_docs(conn, limit=1, offset=1, order="grade")
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["uid"] != second["items"][0]["uid"]


def test_filters_still_apply_with_dedupe(conn):
    got = list_docs(conn, limit=50, region="海南")
    assert got["total"] == 1
    assert got["items"][0]["title"] == "三亚柏悦黄金房号"


def test_facet_counts_match_list_total(conn):
    """侧栏标签上的数必须等于点进去的篇数，否则用户以为丢了数据。"""
    counts = {item["value"]: item["count"] for item in facets(conn)["regions"]}
    for region, count in counts.items():
        assert list_docs(conn, limit=200, region=region)["total"] == count


def test_unique_docs_ignores_copies(conn):
    assert count_unique_docs(conn) == 2, "3 条收录里有一对是副本"
