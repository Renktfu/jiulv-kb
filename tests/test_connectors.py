"""连接层测试：探测、去重、停用、增量同步、回写闭环。

全部用临时目录模拟 WorkBuddy 与 OpenCLaw 的目录形态，不碰真实资料库。
真实布局靠两个环境变量指过来：JIULV_WB_ROOT / JIULV_OC_ROOT。
"""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from jiulv import connectors, db, inbox, sync
from jiulv.config import load_config
from jiulv.search import search

NOTE = """\
### 笔记200：三亚艾迪逊选房避坑
- **标题**：三亚艾迪逊选房避坑
- **地区**：海南·三亚·艾迪逊
- **互动数据**：1200赞 / 3400收藏 / 210评论

艾迪逊的房间按楼层分档，低楼层看的是树不是海。带娃别订套房，泳池在另一栋。
"""

HOOK = """\
# 开头钩子 · 反常识型

## H-R-009
- 原文：三亚住艾迪逊，别选低楼层
- 类型：反常识
- 人群：M1
- 痛点：P2
- 机制：先否掉默认选项，再给具体替代。
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch) -> dict:
    """造一棵假的 WorkBuddy 根和 OpenClaw 根。"""
    wb = tmp_path / "WorkBuddy"
    oc = tmp_path / ".openclaw"

    # 两个批次的资料库，只有新的那个该被自动选中
    write(wb / "2026-01-01-00-00-00" / "小红书酒旅资料库" / "02_地区素材库" / "旧笔记.md", NOTE)
    write(wb / "2026-10-16-16-08-58" / "小红书酒旅资料库" / "02_地区素材库" / "A_200_三亚艾迪逊.md", NOTE)
    write(wb / "2026-10-16-16-08-58" / "小红书酒旅资料库" / "13_钩子库" / "01_开头钩子" / "反常识.md", HOOK)
    old = wb / "2026-01-01-00-00-00" / "小红书酒旅资料库"
    os.utime(old, (time.time() - 86400, time.time() - 86400))

    write(wb / "xhs-bluev-library" / "批次" / "文案.md", NOTE)
    write(oc / "workspace" / "reference" / "房型" / "艾迪逊房型.md", NOTE)
    write(oc / "workspace" / "memory" / "经验.md", NOTE)
    write(oc / "workspace-xhs-writer" / "产出" / "初稿.md", NOTE)
    write(oc / "workspace" / "projects" / "repo" / ".claude" / "rules" / "react.md", "无关技术文档")

    monkeypatch.setenv("JIULV_WB_ROOT", str(wb))
    monkeypatch.setenv("JIULV_OC_ROOT", str(oc))
    return {"wb": wb, "oc": oc, "tmp": tmp_path}


@pytest.fixture()
def config(roots, tmp_path: Path):
    cfg = {"app": {"database": "kb.sqlite3"}, "sources": []}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return load_config(path)


# ------------------------------------------------------------------ 探测


def test_discover_picks_latest_batch_and_lists_both_sides(config, roots):
    found = {c.profile: c for c in connectors.discover(config)}
    assert found["wb-library"].exists
    # 两个批次里取最近改动的那个
    assert "2026-10-16-16-08-58" in found["wb-library"].path
    assert found["wb-library"].docs == 2
    assert found["oc-reference"].exists
    assert found["oc-memory"].exists
    assert found["wb-output"].connected is False


def test_deep_off_skips_projects(config):
    profiles = {c.profile for c in connectors.discover(config)}
    assert "oc-projects" not in profiles
    profiles = {c.profile for c in connectors.discover(config, deep=True)}
    assert "oc-projects" in profiles


def test_projects_excludes_tech_docs(config):
    cand = next(c for c in connectors.discover(config, deep=True) if c.profile == "oc-projects")
    # .claude/rules 下的 md 不算酒旅资料
    assert cand.docs == 0


# ------------------------------------------------------------------ 连接


def test_connect_writes_sources_file(config):
    result = connectors.connect(config)
    assert result["ok"] and result["written"]
    assert Path(result["written"]).name == "sources.yaml"

    refreshed = load_config(config.path)
    names = [s["name"] for s in refreshed.sources]
    assert "WorkBuddy 主资料库" in names
    assert "OpenCLaw 参考库" in names
    assert any(n.startswith("WorkBuddy 产出") for n in names)
    # oc-projects 默认不连
    assert "OpenCLaw 项目库" not in names


def test_connect_does_not_duplicate_existing_path(config, roots):
    """config.yaml 已经手写过同一个目录 → 自动连接必须跳过它。"""
    manual = {
        "app": {"database": "kb.sqlite3"},
        "sources": [
            {
                "name": "我自己的名字",
                "group": "workbuddy",
                "path": str(roots["wb"] / "2026-10-16-16-08-58" / "小红书酒旅资料库"),
                "include": ["**/*.md"],
                "exclude": [],
            }
        ],
    }
    path = config.path
    path.write_text(yaml.safe_dump(manual, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path)
    result = connectors.connect(cfg)
    assert all("WorkBuddy 主资料库" != c["name"] for c in result["connected"])

    refreshed = load_config(path)
    paths = [os.path.realpath(s["path"]) for s in refreshed.sources]
    assert len(paths) == len(set(paths)), "同一目录被收了两次"


def test_connect_dry_run_writes_nothing(config):
    result = connectors.connect(config, dry_run=True)
    assert result["connected"]
    assert not (config.base_dir / "sources.yaml").exists()


def test_disabled_generated_source_is_ignored(config):
    connectors.connect(config)
    file = config.base_dir / "sources.yaml"
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    for src in data["sources"]:
        if src["name"] == "OpenCLaw 记忆":
            src["enabled"] = False
    file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    names = [s["name"] for s in load_config(config.path).sources]
    assert "OpenCLaw 记忆" not in names
    assert "OpenCLaw 参考库" in names


def test_connect_is_idempotent(config):
    first = connectors.connect(config)
    assert first["connected"]
    second = connectors.connect(config)
    assert second["connected"] == []
    assert second["written"] == ""


# ------------------------------------------------------------------ 同步


def test_sync_skips_when_nothing_changed(config):
    connectors.connect(config)
    cfg = load_config(config.path)
    first = sync.sync_once(cfg)
    assert first["skipped"] is False
    assert first["report"]["added"] == 6  # 资料库 2 篇 + 产出 + 参考库 + 记忆 + 工作区

    conn = db.open_db(cfg.db_path)
    total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    conn.close()
    assert total >= 6

    second = sync.sync_once(cfg)
    assert second["skipped"] is True
    assert second["changed"] == []


def test_sync_picks_up_changed_file(config, roots):
    connectors.connect(config)
    cfg = load_config(config.path)
    sync.sync_once(cfg)

    target = roots["oc"] / "workspace" / "reference" / "房型" / "艾迪逊房型.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n低楼层看不到海。\n", encoding="utf-8")
    result = sync.sync_once(cfg)
    assert result["skipped"] is False
    assert result["report"]["updated"] == 1
    assert "OpenCLaw 参考库" in result["changed"]


def test_sync_prunes_deleted_file(config, roots):
    connectors.connect(config)
    cfg = load_config(config.path)
    sync.sync_once(cfg)

    (roots["oc"] / "workspace" / "memory" / "经验.md").unlink()
    result = sync.sync_once(cfg)
    assert result["report"]["removed"] == 1


def test_fingerprint_records_missing_dir(config, tmp_path):
    cfg = load_config(config.path)
    cfg.data["sources"] = [
        {"name": "不存在的库", "group": "x", "path": str(tmp_path / "nope"), "include": [], "exclude": []}
    ]
    assert sync.fingerprint(cfg)["不存在的库"]["files"] == -1


# ------------------------------------------------------------------ 回写


def test_inbox_save_is_searchable(config):
    connectors.connect(config)
    cfg = load_config(config.path)
    result = inbox.save(
        cfg,
        title="三亚艾迪逊带娃实测",
        content="低楼层看不到海，带娃要选高层双床。",
        hotel="三亚艾迪逊",
        region="海南",
        agent="openclaw",
        note="用「楼层」这个具体细节否定默认选项",
    )
    assert result["ok"] and result["uid"]
    assert Path(result["path"]).exists()

    conn = db.open_db(cfg.db_path)
    hits = search(conn, "艾迪逊 低楼层", limit=5)
    conn.close()
    assert hits["total"] >= 1
    assert any("艾迪逊" in h["doc"]["title"] for h in hits["hits"])


def test_inbox_kind_sets_doc_type(config):
    connectors.connect(config)
    cfg = load_config(config.path)
    inbox.save(cfg, title="钩子一条", content="别选低楼层", kind="hook")

    conn = db.open_db(cfg.db_path)
    row = conn.execute(
        "SELECT doc_type, source FROM docs WHERE source=?", (inbox.SOURCE_NAME,)
    ).fetchone()
    conn.close()
    assert row["doc_type"] == "hook"


def test_inbox_rejects_empty_content(config):
    cfg = load_config(config.path)
    with pytest.raises(ValueError):
        inbox.save(cfg, title="空的", content="  ")


def test_sync_sees_inbox_changes_after_direct_write(config):
    """回写走的是「存盘 + 直接收录」，补记指纹后 sync 不能误判成「没变化」。"""
    connectors.connect(config)
    cfg = load_config(config.path)
    sync.sync_once(cfg)

    saved = inbox.save(cfg, title="临时一条", content="低楼层看不到海")
    path = Path(saved["path"])
    assert path.exists()

    # 存了又删：指纹回到原点，但库里多了一篇，sync 必须发现并回收
    path.unlink()
    result = sync.sync_once(cfg)
    assert result["skipped"] is False
    assert result["report"]["removed"] == 1


def test_sync_state_is_written(config):
    connectors.connect(config)
    cfg = load_config(config.path)
    sync.sync_once(cfg)
    state = sync.read_state(cfg)
    assert state["last_sync"]
    assert state["sources"]["OpenCLaw 参考库"]["files"] == 1
    assert sync.state_path(cfg).exists()
