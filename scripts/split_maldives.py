"""把 232 行合并版按小节逐字切成单篇，不改一个字的正文。

旧的 22 篇是改写版（12174 字 vs 原文 6950 字），改写会掺进原文没有的判断，
这里全部作废重切：只做切分，正文与原文逐行一致，脚本末尾自校验。
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path.home() / "Downloads" / "马尔代夫选岛资料库_Candice蒸馏合并版.md"
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "library" / "maldives"

HEAD = """# {title}

> 出自《马尔代夫选岛资料库（Candice蒸馏合并版 v1.0）》
> 地区：马尔代夫
> 切分说明：只按小节切开，正文逐字照录，未改一字

"""

# (文件名, 标题, 起始行, 结束行)，行号对应原文件 1-based 闭区间
SECTIONS = [
    ("00-导读与目录", "马尔代夫资料库 · 导读与目录", [(1, 5), (9, 16), (232, 232)]),
    ("01-时差", "时差：比北京时间慢 3 小时", [(22, 25)]),
    ("02-上岛方式", "上岛方式：快艇 / 内飞加接驳 / 水飞", [(27, 31)]),
    ("03-餐型缩写", "餐型缩写：BB / HB / FB / AI", [(33, 37)]),
    ("04-浮潜评级真相", "浮潜评级真相：双 A 超 A 都是代理自己评的", [(39, 43)]),
    ("05-入境禁忌", "入境禁忌与必带证件", [(45, 50)]),
    ("06-马代一天节奏", "马代一天的正确节奏", [(52, 57)]),
    ("07-浮潜安全", "浮潜安全：肺水肿与呼吸管", [(59, 64)]),
    ("08-水飞滞留与优先级", "水飞滞留与「骑士」真相", [(66, 70)]),
    ("09-拆分预算方法", "拆分预算方法", [(72, 77)]),
    ("10-富豪为什么黏马代", "富豪为什么黏马代：五个维度", [(79, 84)]),
    ("11-岛屿-经济型", "岛屿：经济型 人均 5k–1w", [(88, 100)]),
    ("12-岛屿-中端热门", "岛屿：中端热门 人均 1w–1.5w", [(102, 111)]),
    ("13-岛屿-中高端", "岛屿：中高端品质 人均 1.5w–2.5w", [(113, 124)]),
    ("14-岛屿-顶奢私岛", "岛屿：顶奢私岛 人均 2.5w 以上", [(126, 133)]),
    ("15-快艇岛专项", "快艇岛专项", [(137, 143)]),
    ("16-24小时AI与3A清单", "24 小时 AI 与 3A 一价全包岛清单", [(147, 158)]),
    ("17-单岛-魔富士", "单岛卡：魔富士（康斯丹）", [(162, 171)]),
    ("18-单岛-哈拉微利", "单岛卡：哈拉微利（康斯丹）", [(173, 177)]),
    ("19-单岛-阿丽拉", "单岛卡：阿丽拉（凯悦）", [(179, 183)]),
    ("20-备选海岛", "同赛道备选海岛", [(187, 194)]),
    ("21-爆款公式与争议埋点", "爆款公式与争议埋点", [(198, 213)]),
    ("22-句式库", "句式库：开头、立信任、过渡、收人设、引流", [(215, 220)]),
    ("23-咨询问卷", "咨询问卷：私信直接发客户", [(222, 228)]),
]


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    OUT.mkdir(parents=True, exist_ok=True)

    # 全部作废旧文件，避免改写版和新切版混在库里
    for old in OUT.glob("*.md"):
        old.unlink()

    picked: list[str] = []
    for stem, title, ranges in SECTIONS:
        body: list[str] = []
        for start, end in ranges:
            body.extend(lines[start - 1 : end])
            picked.extend(lines[start - 1 : end])
        text = HEAD.format(title=title) + "\n".join(body).rstrip() + "\n"
        (OUT / f"{stem}.md").write_text(text, encoding="utf-8")
        print(f"{stem:28} {len(body):3} 行")

    # 自校验：切出来的每一行都必须在原文里，且原文实质行不能漏
    norm = lambda s: re.sub(r"\s+", "", s)
    got = {norm(p.read_text(encoding="utf-8")) for p in OUT.glob("*.md")}
    blob = "".join(got)
    src_lines = [l for l in lines if len(l.strip()) >= 12]
    missing = [l for l in src_lines if norm(l) not in blob]
    print(f"\n原文实质行 {len(src_lines)}，未覆盖 {len(missing)}")
    for l in missing:
        print("  MISS:", l[:100])
    if missing:
        raise SystemExit("有行没切进去，检查行号")

    extra = {
        norm(l) for l in picked if len(l.strip()) >= 12
    } - {norm(l) for l in src_lines}
    print(f"自造内容行 {len(extra)}（应为 0）")


if __name__ == "__main__":
    main()
