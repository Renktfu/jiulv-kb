"""组稿：把「写一篇三亚亲子酒店文案」变成一份可直接投喂给任何大模型的上下文包。

这是整个库的主要出口。检索只解决「找得到」，组稿解决「写得出」：
规则 + 钩子 + 同地区爆款结构 + 可核对事实 + 输出约束，一次给全。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import search as search_mod
from .config import Config
from .extract import excerpt_for
from .taxonomy import load_taxonomy

GOAL_LABEL = {
    "save": "收藏",
    "comment": "评论",
    "consult": "私信咨询",
    "share": "转发",
    "收藏": "收藏",
    "评论": "评论",
    "咨询": "私信咨询",
    "转发": "转发",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _resolve_code(raw: str, table: Dict[str, Any]) -> Optional[str]:
    """把「带娃」「M2」「亲子」统一解析成 M2 这样的码。"""
    if not raw:
        return None
    text = str(raw).strip()
    if text.upper() in table:
        return text.upper()
    for code, item in table.items():
        aliases = item.get("aliases", []) if isinstance(item, dict) else []
        label = item.get("label", "") if isinstance(item, dict) else str(item)
        if text in aliases or text == label or (label and text in label):
            return code
        if any(alias and alias in text for alias in aliases):
            return code
    return None


def _trim(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _title_key(title: str) -> str:
    return re.sub(r"[\s，,。！!？?、·「」“”\"'（）()【】]+", "", title or "")


def _terms_of(brief: Dict[str, Any], taxonomy: Dict[str, Any]) -> List[str]:
    raw = [
        brief.get("hotel"),
        brief.get("region"),
        taxonomy["audience"].get(brief.get("audience") or "", {}).get("label", ""),
        taxonomy["pain"].get(brief.get("pain") or "", {}).get("label", ""),
        brief.get("angle"),
    ]
    out: List[str] = []
    for item in raw:
        if not item:
            continue
        out.append(item)
        out.extend([w for w in re.split(r"[\s/、]+", item) if len(w) >= 2])
    return list(dict.fromkeys(out))


def _grade_rank(grade: Optional[str]) -> int:
    return {"S": 0, "A": 1, "B": 2}.get((grade or "").upper(), 3)


def _metrics(row) -> Dict[str, Any]:
    try:
        return json.loads(row["metrics_json"] or "{}")
    except Exception:
        return {}


def _pick_refs(
    conn,
    config: Config,
    brief: Dict[str, Any],
    taxonomy: Dict[str, Any],
    max_refs: int,
    excerpt_chars: int,
) -> List[Dict[str, Any]]:
    hotel = brief.get("hotel") or ""
    region = brief.get("region") or ""
    terms = _terms_of(brief, taxonomy)

    candidates: List[tuple] = []  # (排序键, row, 来源说明)
    if hotel:
        for row in conn.execute(
            "SELECT * FROM docs WHERE hotel LIKE ? AND doc_type IN ('note','guide','fact') "
            "ORDER BY id DESC LIMIT 40",
            (f"%{hotel}%",),
        ):
            candidates.append(((0, _grade_rank(row["grade"]), -_metrics(row).get("comments", 0)), row, f"同酒店·{hotel}"))
    if region:
        for row in conn.execute(
            "SELECT * FROM docs WHERE region = ? AND doc_type IN ('note','guide') ORDER BY id DESC LIMIT 120",
            (region,),
        ):
            candidates.append(((1, _grade_rank(row["grade"]), -_metrics(row).get("comments", 0)), row, f"同地区爆款·{region}"))

    if len(candidates) < 3:
        query = " ".join(x for x in [hotel, region, brief.get("pain_label"), brief.get("angle")] if x)
        if query:
            result = search_mod.search(conn, query, config=config, limit=max_refs * 2, mode="keyword")
            for hit in result["hits"]:
                row = conn.execute("SELECT * FROM docs WHERE uid=?", (hit["doc"]["uid"],)).fetchone()
                if row:
                    candidates.append(((2, _grade_rank(row["grade"]), 0), row, "关键词兜底"))

    candidates.sort(key=lambda item: item[0])
    seen: set = set()
    refs: List[Dict[str, Any]] = []
    for _, row, reason in candidates:
        key = _title_key(row["title"]) or row["uid"]
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "uid": row["uid"],
                "title": row["title"],
                "doc_type": row["doc_type"],
                "grade": row["grade"],
                "region": row["region"],
                "hotel": row["hotel"],
                "author": row["author"],
                "metrics": _metrics(row),
                "snippet": excerpt_for(row["content"], terms, excerpt_chars),
                "path": row["rel_path"],
                "reason": reason,
            }
        )
        if len(refs) >= max_refs:
            break
    return refs


RULE_HINT_RE = re.compile(r"规则|手册|规范|风格|模板|原则|指南")


def _pick_rules(conn, config: Config, brief: Dict[str, Any], excerpt_chars: int,
                taxonomy: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    terms = _terms_of(brief, taxonomy or {"audience": {}, "pain": {}})
    rows = conn.execute(
        "SELECT * FROM docs WHERE doc_type='rule' "
        "ORDER BY (CASE WHEN title LIKE '%规则%' OR title LIKE '%手册%' OR title LIKE '%风格%' THEN 0 ELSE 1 END),"
        " char_count DESC LIMIT 12"
    ).fetchall()
    # 有的库把范文误标成规则，标题看不出是规则的先排后面，且最多取一条
    primary = [r for r in rows if RULE_HINT_RE.search(r["title"] or "") or RULE_HINT_RE.search(r["rel_path"] or "")]
    secondary = [r for r in rows if r not in primary]

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in primary + secondary[:1]:
        key = _title_key(row["title"]) or row["uid"]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "uid": row["uid"],
                "title": row["title"],
                "snippet": excerpt_for(row["content"], terms + ["必须", "不写", "禁止"], excerpt_chars),
                "path": row["rel_path"],
            }
        )
        if len(out) >= 3:
            break
    return out


def _pick_hooks(conn, brief: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    pool = search_mod.list_hooks(conn, limit=40)
    audience = brief.get("audience") or ""
    pain = brief.get("pain") or ""
    hook_type = brief.get("hook_type") or ""

    def rank(hook: Dict[str, Any]) -> tuple:
        audience_hit = 0 if (audience and audience in (hook["audience"] or "")) else 1
        pain_hit = 0 if (pain and pain in (hook["pain"] or "")) else 1
        type_hit = 0 if (hook_type and hook_type in (hook["hook_type"] or "")) else 1
        verified = 0 if ("验证" in (hook["status"] or "") and "待" not in (hook["status"] or "")) else 1
        return (audience_hit, pain_hit, type_hit, verified, hook["id"])

    ordered = sorted(pool, key=rank)
    picked = [h for h in ordered if rank(h)[:3] == (0, 0, 0)][: max(1, limit - 1)]
    for hook in ordered:
        if len(picked) >= limit:
            break
        if hook not in picked:
            picked.append(hook)

    return [
        {
            "code": h["code"],
            "text": h["text"],
            "type": h["hook_type"],
            "audience": h["audience"],
            "pain": h["pain"],
            "mechanism": h["mechanism"],
            "conversion": h["conversion"],
            "status": h["status"],
            "bucket": h["bucket"],
        }
        for h in picked[:limit]
    ]


def _pick_facts(conn, brief: Dict[str, Any], limit: int = 5, excerpt_chars: int = 320,
                taxonomy: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    hotel = brief.get("hotel") or ""
    region = brief.get("region") or ""
    terms = _terms_of(brief, taxonomy or {"audience": {}, "pain": {}})
    rows = []
    if hotel:
        rows.extend(
            conn.execute(
                "SELECT * FROM docs WHERE doc_type IN ('fact','guide') AND hotel LIKE ? LIMIT 6",
                (f"%{hotel}%",),
            ).fetchall()
        )
    if region and len(rows) < limit:
        rows.extend(
            conn.execute(
                "SELECT * FROM docs WHERE doc_type IN ('fact','guide') AND region = ? "
                "AND (title LIKE '%酒店%' OR title LIKE '%房型%' OR title LIKE '%选房%' OR title LIKE '%产品%' "
                "OR title LIKE '%知识%' OR title LIKE '%攻略%') LIMIT 8",
                (region,),
            ).fetchall()
        )
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        key = _title_key(row["title"]) or row["uid"]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "uid": row["uid"],
                "title": row["title"],
                "hotel": row["hotel"],
                "region": row["region"],
                "snippet": excerpt_for(row["content"], terms, excerpt_chars),
                "path": row["rel_path"],
            }
        )
        if len(out) >= limit:
            break
    return out


def compose(
    conn,
    config: Config,
    brief: Dict[str, Any],
    save_log: bool = True,
) -> Dict[str, Any]:
    taxonomy = load_taxonomy()
    audience_code = _resolve_code(brief.get("persona") or brief.get("audience") or "", taxonomy["audience"])
    pain_code = _resolve_code(brief.get("pain") or "", taxonomy["pain"])
    hook_type = (brief.get("hook_type") or "").strip()
    if not hook_type:
        hook_type = "反常识" if pain_code in ("P2", "P4") else "身份代入"

    normalized = {
        "hotel": (brief.get("hotel") or "").strip(),
        "region": (brief.get("region") or "").strip(),
        "audience": audience_code or "",
        "audience_label": taxonomy["audience"].get(audience_code or "", {}).get("label", "")
        or (brief.get("persona") or ""),
        "pain": pain_code or "",
        "pain_label": taxonomy["pain"].get(pain_code or "", {}).get("label", "")
        or (brief.get("pain") or ""),
        "hook_type": hook_type,
        "goal": GOAL_LABEL.get(str(brief.get("goal") or "save"), brief.get("goal") or "收藏"),
        "platform": brief.get("platform") or "小红书",
        "angle": (brief.get("angle") or "").strip(),
        "style": brief.get("style") or config.get("compose", "default_style", default="瑞克克"),
        "extra_facts": [f for f in (brief.get("extra_facts") or []) if f],
    }

    max_refs = int(brief.get("max_references") or config.get("compose", "max_references", default=6))
    excerpt_chars = int(config.get("compose", "excerpt_chars", default=600))

    refs = _pick_refs(conn, config, normalized, taxonomy, max_refs, excerpt_chars)
    rules = _pick_rules(conn, config, normalized, excerpt_chars, taxonomy)
    hooks = _pick_hooks(conn, normalized)
    facts = _pick_facts(conn, normalized, limit=5, excerpt_chars=min(excerpt_chars, 320), taxonomy=taxonomy)
    guardrails = taxonomy.get("guardrails") or []

    warnings: List[str] = []
    if normalized["hotel"] and not any("同酒店" in r["reason"] for r in refs):
        warnings.append(
            f"库里没有「{normalized['hotel']}」的直接资料，只有同地区参考。"
            "写具体房型、服务、景观时必须先补采集，否则只能写到地区层面。"
        )
    if not refs:
        warnings.append("这次没检索到任何参考，建议先跑采集，不要凭空写。")
    if not hooks:
        warnings.append("钩子库没有匹配条目，本次按通用结构写，写完记得回填一条新钩子。")

    prompt = render_prompt(normalized, rules, hooks, refs, facts, guardrails)
    compose_id = f"c_{uuid.uuid4().hex[:12]}"

    if save_log:
        conn.execute(
            "INSERT INTO compose_log(id, brief_json, created_at) VALUES(?,?,?)",
            (compose_id, json.dumps(normalized, ensure_ascii=False), _now()),
        )
        conn.commit()

    return {
        "compose_id": compose_id,
        "brief": normalized,
        "warnings": warnings,
        "style_rules": rules,
        "hooks": hooks,
        "references": refs,
        "facts": facts,
        "guardrails": guardrails,
        "prompt": prompt,
        "coverage": {
            "references": len(refs),
            "rules": len(rules),
            "hooks": len(hooks),
            "facts": len(facts),
        },
    }


def render_prompt(
    brief: Dict[str, Any],
    rules: List[Dict[str, Any]],
    hooks: List[Dict[str, Any]],
    refs: List[Dict[str, Any]],
    facts: List[Dict[str, Any]],
    guardrails: List[str],
) -> str:
    lines: List[str] = []
    lines.append("你是小红书酒旅方向的文案写手，帮酒店代订写能爆的住宿笔记。")
    lines.append("下面给了你风格规则、同地区爆款结构、可改写的钩子、可核对的事实。")
    lines.append("只准用这些资料，资料里没有的一律不写。")
    lines.append("")
    lines.append("## 一、本次任务")
    lines.append(f"- 酒店：{brief['hotel'] or '（未指定，按地区写）'}")
    lines.append(f"- 地区：{brief['region'] or '（未指定）'}")
    lines.append(f"- 人群：{brief['audience_label'] or '（未指定）'}")
    lines.append(f"- 核心痛点：{brief['pain_label'] or '（未指定）'}")
    lines.append(f"- 钩子类型：{brief['hook_type']}")
    lines.append(f"- 期望动作：{brief['goal']}")
    lines.append(f"- 平台：{brief['platform']}")
    if brief.get("angle"):
        lines.append(f"- 本篇切入角度：{brief['angle']}")
    lines.append(f"- 风格档案：{brief['style']}")
    lines.append("")

    lines.append("## 二、硬规则（违反即不合格）")
    for rule in guardrails:
        lines.append(f"- {rule}")
    lines.append("")

    if rules:
        lines.append("## 三、风格规则节选（照这个语感写）")
        for item in rules:
            lines.append(f"### {item['title']}")
            lines.append(item["snippet"])
            lines.append("")

    if hooks:
        lines.append("## 四、可直接改写的钩子（选一条当开头）")
        for hook in hooks:
            meta = "｜".join(
                x for x in [hook["type"], hook["audience"], hook["pain"]] if x
            )
            lines.append(f"- {hook['code']}（{meta}）：{hook['text']}")
            if hook.get("mechanism"):
                lines.append(f"  - 机制：{hook['mechanism']}")
        lines.append("")

    if refs:
        lines.append("## 五、同地区/同酒店爆款结构（学结构，不要抄字）")
        for idx, ref in enumerate(refs, 1):
            metrics = ref.get("metrics") or {}
            stat = "／".join(
                f"{int(v)}{k}" for k, v in
                [("赞", metrics.get("likes", 0)), ("藏", metrics.get("saves", 0)), ("评", metrics.get("comments", 0))]
                if v
            )
            label = "／".join(x for x in [ref.get("grade") and f"{ref['grade']}级", ref.get("reason")] if x)
            lines.append(f"{idx}. 《{ref['title']}》{('（' + stat + '）') if stat else ''} — {label}")
            if ref.get("snippet"):
                lines.append(f"   参考片段：{ref['snippet']}")
        lines.append("")

    if facts or brief.get("extra_facts"):
        lines.append("## 六、事实清单（只能写这里有的）")
        for fact in facts:
            owner = fact.get("hotel") or fact.get("region") or "通用"
            lines.append(f"- 【{owner}｜{fact['title']}】{fact['snippet']}")
        for fact in brief.get("extra_facts") or []:
            lines.append(f"- 【用户补充】{fact}")
        others = [
            f for f in facts
            if brief.get("hotel") and f.get("hotel") and brief["hotel"] not in f["hotel"]
        ]
        if others:
            lines.append("")
            lines.append(
                "注意：标注为其他酒店的条目只用来参考「怎么写」，事实不能挪到本篇酒店身上。"
            )
        lines.append("")

    lines.append("## 七、输出要求")
    lines.append("1. 先给 3 个标题备选，每个不超过 20 字，至少一个用上面的钩子句式。")
    lines.append("2. 正文 500-800 字，第一人称，像跟朋友聊天，不要小标题堆砌。")
    lines.append("3. 必须写到具体的房间、景观、服务或餐饮细节，且这些细节能在事实清单里找到出处。")
    lines.append("4. 好和坏都要写，不能全夸也不能全骂。")
    lines.append("5. 不出现具体价格数字，用「千元档」「不便宜」这类相对说法。")
    lines.append(f"6. 结尾给一个让人想{brief['goal']}的动作句，再留一个能引发评论的问题。")
    lines.append("7. 正文后单独列「本篇事实出处」，逐条对应上面的资料标题，方便我核对。")
    return "\n".join(lines)


def generate(config: Config, prompt: str) -> Dict[str, Any]:
    """可选：服务端直接调对话模型出稿。不配置就走 Agent 侧生成。"""
    if not config.get("generation", "enabled", default=False):
        raise RuntimeError("未启用 generation.enabled，请让 Agent 直接用 prompt 生成")
    base_url = str(config.get("generation", "base_url", default="")).rstrip("/")
    model = config.get("generation", "model", default="")
    env_name = config.get("generation", "api_key_env", default="JIULV_LLM_API_KEY")
    key = os.environ.get(env_name or "", "")
    if not key:
        raise RuntimeError(f"未设置 {env_name}")

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(config.get("generation", "temperature", default=0.85)),
            "max_tokens": int(config.get("generation", "max_tokens", default=1600)),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"模型接口返回 {exc.code}：{exc.read().decode('utf-8', 'ignore')[:200]}") from exc
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return {"model": model, "text": text, "usage": data.get("usage", {})}
