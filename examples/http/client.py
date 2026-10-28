"""REST 客户端示例：不接 MCP 也能用，任何语言照着这段抄就行。

跑之前先把服务起起来：jiulv serve --port 8787
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
API_KEY = ""  # config.yaml 里配了 security.api_keys 才需要填


def call(path: str, payload: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            **({"X-API-Key": API_KEY} if API_KEY else {}),
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{error.code}: {error.read().decode('utf-8', 'ignore')}")


if __name__ == "__main__":
    # 1) 先看库里有没有这家酒店的资料
    found = call("/api/v1/search?q=%E4%B8%89%E4%BA%9A%20%E4%BB%81%E6%81%92%E7%9A%87%E5%86%A0&limit=3")
    print("命中", found["total"], "篇")
    for hit in found["hits"]:
        print(" -", hit["doc"]["title"], hit["doc"]["grade"] or "")

    # 2) 组稿：拿一份可以直接投喂给大模型的上下文包
    pack = call(
        "/api/v1/compose",
        {
            "hotel": "三亚仁恒皇冠",
            "region": "海南",
            "persona": "M2",
            "pain": "P2",
            "hook_type": "反常识",
            "goal": "save",
        },
    )
    print("\n=== 上下文包 ===")
    print(pack["prompt"])
    print("\n用到的资料：")
    for reference in pack["references"]:
        print(" -", reference["reason"], "|", reference["title"])

    # 3) 发完之后回填数据，用来复盘哪类钩子有效
    call(
        "/api/v1/feedback",
        {
            "kind": "compose",
            "ref": pack["compose_id"],
            "hotel": "三亚仁恒皇冠",
            "hook_type": "反常识",
            "likes": 120,
            "saves": 300,
            "comments": 45,
            "is_hit": True,
        },
    )
