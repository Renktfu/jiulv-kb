"""地区判定与来源分类。

马尔代夫曾经混在「东南亚」里，侧栏只有东南亚一个口，马代的笔记要先想到它属于
东南亚才找得到——资料明明在库里，看着像没收。这里把这几条规则钉住。
"""

from __future__ import annotations

from jiulv.extract import classify, extract_meta


def test_maldives_is_its_own_region():
    text = "# 马尔代夫选岛\n\n水飞下午 3 点后不飞，马累过渡一晚，环礁分布。\n"
    meta = extract_meta(text, "maldives/选岛.md", "选岛", "自有资料库")
    assert meta.region == "马尔代夫"


def test_maldives_no_longer_counted_as_southeast_asia():
    # 一句带过「比马代便宜」的普吉篇，主体还是普吉，不能被马代抢走
    text = "普吉岛选酒店\n\n普吉的海不如马代，但便宜很多。普吉、普吉、普吉。\n"
    meta = extract_meta(text, "东南亚/普吉.md", "普吉", "小红书酒旅资料库")
    assert meta.region == "东南亚"


def test_index_pages_have_no_region():
    # 索引页罗列全库标题，必然提到所有地区；给它挂地区，侧栏点进去全是目录
    text = "# 精选索引\n\n- 三亚\n- 丽江\n- 马尔代夫\n- 长白山\n- 西安\n"
    meta = extract_meta(text, "03_爆款精选索引/索引.md", "索引", "小红书酒旅资料库")
    assert meta.doc_type == "index"
    assert meta.region == ""


def test_agent_memory_has_no_region():
    # 工作日志里提一句马尔代夫，不代表它是马代笔记
    text = "# 2026-10-27\n\n今天整理了马尔代夫资料库，水飞、马累、环礁写了一批。\n"
    meta = extract_meta(text, "2026-10-27.md", "2026-10-27", "OpenCLaw 记忆")
    assert meta.region == ""


def test_library_source_classified_as_note():
    # 没有目录约定可依赖，靠来源名认，否则落到 misc
    assert classify("maldives/01-上岛方式.md", "正文", "自有资料库") == "note"


def test_explicit_region_field_wins_over_detection():
    # 手写的「地区：」优先，不做关键词推断
    text = "> 地区：马尔代夫\n\n正文里顺带提了一句三亚和海南。\n"
    meta = extract_meta(text, "maldives/某篇.md", "某篇", "自有资料库")
    assert meta.region == "马尔代夫"
