"""接口与 MCP 协议测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from jiulv import db
from jiulv.api import create_app
from jiulv.config import load_config
from jiulv.ingest import ingest
from jiulv.mcp_server import Server


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "S_001_三亚瑞吉吐槽.md").write_text(
        "# 三亚瑞吉吐槽\n\n- **标题**：三亚瑞吉吐槽\n- **地区**：海南·三亚·瑞吉\n"
        "- **互动数据**：46赞 / 20收藏 / 162评论\n\n电动门设计反人类，右门先开，三秒后左门再开。\n"
        "防蚊也敷衍，一个月才消杀一次。服务不错，但硬件看得出在省成本。\n",
        encoding="utf-8",
    )
    (corpus / "H-R-001.md").write_text(
        "# 钩子\n\n## H-R-001\n- 原文：这家酒店最贵的房型，反而是最不划算的\n- 类型：反常识\n"
        "- 人群：C1\n- 痛点：P4\n- 机制：违背贵等于好的直觉\n- 转化：收藏\n- 来源：自有\n- 状态：待验证\n",
        encoding="utf-8",
    )
    cfg = {
        "app": {"database": "kb.sqlite3"},
        "security": {"api_keys": [], "allow_localhost_without_key": True},
        "sources": [
            {"name": "测试库", "group": "test", "path": str(corpus), "include": ["**/*.md"], "exclude": []}
        ],
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    config = load_config(cfg_path)
    conn = db.open_db(config.db_path)
    ingest(config, conn, rebuild=True)
    conn.close()

    app = create_app(str(cfg_path))
    return TestClient(app)


def test_health(client: TestClient):
    data = client.get("/api/v1/health").json()
    assert data["ok"] is True
    assert data["docs"] == 2


def test_stats(client: TestClient):
    data = client.get("/api/v1/stats").json()
    assert data["docs"] == 2
    assert data["hooks"] == 1
    assert "regions" in data["facets"]


def test_search_endpoint(client: TestClient):
    data = client.get("/api/v1/search", params={"q": "瑞吉"}).json()
    assert data["total"] >= 1
    assert "瑞吉" in data["hits"][0]["doc"]["title"]


def test_doc_endpoint(client: TestClient):
    listing = client.get("/api/v1/docs").json()
    uid = listing["items"][0]["uid"]
    doc = client.get(f"/api/v1/docs/{uid}").json()
    assert doc["content"]
    assert client.get(f"/api/v1/docs/{uid}/raw").status_code == 200


def test_hooks_endpoint(client: TestClient):
    data = client.get("/api/v1/hooks", params={"hook_type": "反常识"}).json()
    assert data["total"] == 1
    assert data["items"][0]["code"] == "H-R-001"


def test_compose_endpoint(client: TestClient):
    data = client.post(
        "/api/v1/compose",
        json={"hotel": "三亚瑞吉", "region": "海南", "persona": "C1", "pain": "P4", "hook_type": "反常识"},
    ).json()
    assert "prompt" in data
    assert data["coverage"]["references"] >= 1
    assert data["compose_id"].startswith("c_")


def test_feedback_endpoint(client: TestClient):
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "compose", "ref": "c_test", "hook_type": "反常识", "likes": 120, "saves": 300, "is_hit": True},
    ).json()
    assert response["ok"] is True
    outcomes = client.get("/api/v1/outcomes").json()
    assert outcomes["items"][0]["likes"] == 120
    assert outcomes["by_hook_type"][0]["hook_type"] == "反常识"


def test_openapi_available(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/compose" in schema["paths"]
    assert "/api/v1/search" in schema["paths"]


# ------------------------------------------------------------------ MCP


def test_mcp_handshake_and_tools(client: TestClient, tmp_path: Path):
    app = client.app
    server = Server(app.state.config)

    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "jiulv-kb"

    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [tool["name"] for tool in tools["result"]["tools"]]
    assert "kb_context_pack" in names
    assert "kb_search" in names

    call = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "kb_search", "arguments": {"query": "瑞吉"}},
        }
    )
    payload = json.loads(call["result"]["content"][0]["text"])
    assert payload["total"] >= 1

    unknown = server.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert unknown["error"]["code"] == -32601


def test_mcp_notification_gets_no_response(client: TestClient):
    server = Server(client.app.state.config)
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
