"""function calling 循环示例：让任何 OpenAI 兼容模型用上这套库。

这是「不接 MCP 也能用」的完整样板。DeepSeek、Kimi、豆包、通义、智谱、
本地 Ollama，只要是 OpenAI 兼容接口，改三个常量就能跑。

跑之前：
  1. 起服务：jiulv start
  2. 填环境变量：export JIULV_LLM_KEY=sk-xxx
  3. python examples/function-calling/agent_loop.py "给三亚仁恒皇冠写一篇带娃选房笔记"

设计上有一个关键取舍：工具的执行统一走 POST /api/v1/tool/{name}，
不做「工具名 → REST 端点」的映射。这样 schema、参数、返回值三样跟 MCP 完全一致，
同一套工具在两条路线上行为一致，不用维护两份代码。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------- 改这三个

KB_BASE = "http://127.0.0.1:8787"                      # 知识库地址
LLM_BASE = "https://api.deepseek.com/v1"               # 模型接口，OpenAI 兼容即可
LLM_MODEL = "deepseek-chat"
LLM_KEY = os.environ.get("JIULV_LLM_KEY", "")
API_KEY = os.environ.get("JIULV_API_KEY", "")          # 知识库配了 api_keys 才需要

MAX_ROUNDS = 8          # 防止模型在两个工具之间反复横跳
TIMEOUT = 60

SYSTEM_PROMPT = """你有一套酒旅文案知识库，通过 kb_* 工具访问。

写任何酒店笔记之前，先调 kb_context_pack 拿上下文包，按它返回的 prompt 写正文。
prompt 里已经包含风格规则、钩子、参考结构和输出约束，不要再自己另起一段描述需求。

拿到包之后，先把 warnings 念给我听。如果出现「库里没有这家酒店的直接资料」，
停下来问我，不要硬写。

写到具体楼层、房型、朝向、接机时长这类事实时，先用 kb_search 或 kb_get_doc 确认。
库里有的才写，库里没有的一律不写。宁可写少，不写错。

写完调 kb_save 存档，note 一栏写清这篇为什么这么写。"""


def http_json(url: str, payload: Any = None, headers: dict[str, str] | None = None) -> Any:
    """一个够用的 JSON 请求。不引第三方库，是为了让这段代码能直接抄进任何项目。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def kb_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def load_tools() -> list[dict[str, Any]]:
    """工具 schema 直接从服务拿，不落文件。

    想省 token 就只保留用得上的那几个：
        wanted = {"kb_context_pack", "kb_search", "kb_get_doc", "kb_save"}
        return [t for t in tools if t["function"]["name"] in wanted]
    """
    return http_json(f"{KB_BASE}/api/v1/tools", headers=kb_headers())


def run_tool(name: str, arguments: dict[str, Any]) -> str:
    """执行一个工具。返回字符串，因为 tool 消息的 content 只能是字符串。"""
    try:
        result = http_json(
            f"{KB_BASE}/api/v1/tool/{name}",
            payload=arguments,
            headers=kb_headers(),
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "ignore")
        return json.dumps({"ok": False, "error": f"{error.code} {detail}"}, ensure_ascii=False)
    except Exception as error:  # 网络问题也要回给模型，不要直接崩
        return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    return http_json(
        f"{LLM_BASE}/chat/completions",
        payload={
            "model": LLM_MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.85,
        },
        headers={"Authorization": f"Bearer {LLM_KEY}"},
    )


def main() -> int:
    if not LLM_KEY:
        print("先设 JIULV_LLM_KEY 环境变量（模型接口的 key）")
        return 1

    task = " ".join(sys.argv[1:]) or "用 kb_stats 看看库里有多少篇资料"
    tools = load_tools()
    print(f"已加载 {len(tools)} 个工具，模型 {LLM_MODEL}\n")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for round_no in range(1, MAX_ROUNDS + 1):
        response = chat(messages, tools)
        message = response["choices"][0]["message"]
        calls = message.get("tool_calls") or []

        # 工具的入参要看得到，否则出问题不知道是模型给错了还是库返回错了
        for call in calls:
            print(f"[{round_no}] → {call['function']['name']}({call['function']['arguments']})")

        messages.append(message)

        if not calls:
            print("\n=== 成稿 ===\n")
            print(message.get("content") or "（模型没有输出正文）")
            return 0

        for call in calls:
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = run_tool(call["function"]["name"], arguments)
            # 截个长度看看，别把几万字的原文都打到终端
            preview = result[:160].replace("\n", " ")
            print(f"    ← {preview}{'…' if len(result) > 160 else ''}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                }
            )

    print(f"到 {MAX_ROUNDS} 轮还没收敛，停手。任务可能有歧义，换个说法再试。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
