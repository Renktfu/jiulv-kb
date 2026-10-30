"""启动脚本本身的静态检查。

.sh / .bat 里的错不会在 pytest 里现形，只在别人双击的那一刻现形——而那一刻
用户只看到一个闪过去的窗口，或者一句看不懂的 unbound variable。

这里钉的是两条踩过的规矩，都很小，但每一条都能让「双击没反应」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "jiulv-start.command"


def test_no_variable_glued_to_chinese():
    """$status。 这种写法会炸。

    bash 把 $ 后面连续的字符都当变量名。紧贴中文标点时，UTF-8 的那几个字节
    会被算进名字里，于是 `status。` 成了一个没定义过的变量，配上 set -u 就是
    `status?: unbound variable`，第 46 行直接退出——本该打印诊断信息的地方，
    变成了另一句看不懂的报错。要写成 ${status}。
    """
    pattern = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]")
    hits = []
    for no, line in enumerate(SH.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        hits += [(no, m.group(0)) for m in pattern.finditer(line)]
    assert hits == [], f"变量后面紧跟中文，要写成 ${{var}}：{hits}"


def test_launcher_keeps_window_on_failure():
    """失败时不能让人看到窗口一闪就没——必须停住等回车。"""
    text = SH.read_text(encoding="utf-8")
    assert "read -r _" in text
    assert "set -u" in text


def test_docs_never_name_a_launcher_without_its_number():
    """文档里写启动器名字，必须带 01- 编号。

    启动器在包里叫 `01-双击启动.command`，编号是为了让人一眼挑出来。文档里
    少写这个 01-，用户照着念就在文件夹里找到一个不存在的名字，只能挨个点。
    这里只看反引号包起来的名字——正文里复盘历史的那句「以前写错过」用中文
    引号，不会误伤。
    """
    pattern = re.compile(r"(?<!01-)`(双击启动\.(?:command|bat))`")
    targets = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))
    hits = []
    for path in targets:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                hits.append(f"{path.name}:{no} {m.group(1)}")
    assert hits == [], f"启动器名字漏了 01- 编号：{hits}"
