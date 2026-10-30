"""分发包的清单与打包行为。

这里钉的不是「代码跑得对不对」，是「包打得全不全」。这类错误比逻辑错误更阴：
包能打出来、体积看着正常、双击也能启动，但缺了一个文件，症状要在别人机器上
第一次用到那个功能时才出现。所以清单本身也要被测试盯着。

用例自带测试：先按 PAYLOAD 复制一份，再逐项断言里面有源码、没缓存、没真实数据。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    """scripts/ 不是包，按路径加载。"""
    path = ROOT / "scripts" / "make_dist.py"
    spec = importlib.util.spec_from_file_location("_make_dist", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_make_dist"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


md = _load()


def test_payload_entries_exist():
    """清单里每一项都得真的在仓库里。

    改文件名、挪目录之后忘了同步这里，打包会「静默少一块」——build 只会
    打一行「跳过（源里没有）」，然后一路绿到底。这条断言把那种情况变成失败。
    """
    missing = [item for item in md.PAYLOAD if not (ROOT / item).exists()]
    assert missing == []


def test_ignore_means_ignore(tmp_path):
    """shutil.copytree 的 ignore 回调返回的是「要忽略的」，不是「要保留的」。

    这条曾经写反：返回了保留列表，结果包里只剩 __pycache__ 的 pyc，
    一行源码都没有。体积 0.4MB 看着还挺合理，不打开看根本发现不了。
    """
    names = ["api.py", "__pycache__", "web", "taxonomy.yaml", "Thumbs.db", "x.pyc"]
    dropped = md._ignore(str(tmp_path), names)
    assert sorted(dropped) == ["Thumbs.db", "__pycache__", "x.pyc"]
    for keep in ("api.py", "web", "taxonomy.yaml"):
        assert keep not in dropped


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    md.build(out, clean=False)
    return out


def test_both_platforms_built(dist):
    for name in ("macos", "windows", "先读我.txt"):
        assert (dist / name).exists()


def test_source_code_present(dist):
    """源码必须在。少了任何一个模块，双击都会在 import 那一步炸。"""
    for module in ("api.py", "cli.py", "launcher.py", "platform_compat.py", "mcp_server.py"):
        assert (dist / "macos" / "jiulv" / module).is_file()
    assert (dist / "macos" / "jiulv" / "taxonomy.yaml").is_file()
    assert (dist / "macos" / "jiulv" / "web" / "index.html").is_file()
    assert (dist / "macos" / "jiulv" / "web" / "app.js").is_file()


def test_no_build_residue(dist):
    """缓存、虚拟环境、真实库都不能进包。

    真实库这条最重要：data/kb.sqlite3 里是客户酒店名和本机绝对路径，
    跟着包发出去就是泄漏。
    """
    for pattern in ("**/__pycache__", "**/*.pyc", "**/.venv", "**/*.sqlite3*", "**/.git"):
        hits = list(dist.glob(pattern))
        assert hits == [], f"{pattern} 不该出现在包里：{hits[:3]}"
    for leaked in ("config.yaml", "sources.yaml"):
        assert not (dist / "macos" / leaked).exists()
        assert not (dist / "windows" / leaked).exists()


def test_launchers_in_place(dist):
    """启动器要放在包根，不能只在 scripts/ 里——用户不会往子目录找。"""
    for platform, src_name, dst_name in (
        ("macos", "jiulv-start.command", "01-双击启动.command"),
        ("windows", "jiulv-start.bat", "01-双击启动.bat"),
    ):
        assert (dist / platform / dst_name).is_file()
        # scripts/ 里也留一份原名的，方便命令行调用
        assert (dist / platform / "scripts" / src_name).is_file()
    assert (dist / "macos" / "scripts" / "setup.py").is_file()


def test_readme_names_the_real_launcher(dist):
    """说明里写的那个文件名，包里必须真有。

    这条是踩出来的：启动器叫 jiulv-start.command，说明里却写「双击
    「双击启动.command」」。用户照着念找不到，只能挨个文件点。名字对不对得上
    不是文案问题，是能不能打开的问题。
    """
    for platform, readme, name in (
        ("macos", "02-使用说明.txt", "01-双击启动.command"),
        ("windows", "02-使用说明.txt", "01-双击启动.bat"),
    ):
        text = (dist / platform / readme).read_text(encoding="utf-8-sig")
        assert name in text, f"{platform} 说明里没提 {name}"
        assert (dist / platform / name).is_file()


def test_windows_bat_uses_crlf(dist):
    """批处理必须是 CRLF。

    cmd 的老解析器对孤立的 LF 有已知问题，尤其是括号块里的多行 if——
    而这个 .bat 里正好有。仓库里存 LF 是为了 diff 好读，打包时换回 CRLF。
    """
    raw = (dist / "windows" / "01-双击启动.bat").read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_readme_txt_is_bom_crlf(dist):
    """中文 txt 带 BOM + CRLF，老记事本才不乱码、不并成一行。"""
    targets = [dist / "先读我.txt"]
    targets += [dist / p / "02-使用说明.txt" for p in ("macos", "windows")]
    for path in targets:
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{path} 缺 BOM"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{path} 不是 CRLF"


def test_data_dir_is_empty_shell(dist):
    """data/ 只留 .gitkeep 和 library/。

    带着索引一起发会让新环境的库和真实路径错位，所以 kb.sqlite3 一律不带。
    library/ 是例外：里面是拆好篇的自有资料，不是索引，带着新包开箱就有东西可搜。
    """
    data = dist / "macos" / "data"
    assert data.is_dir()
    assert sorted(p.name for p in data.iterdir()) == [".gitkeep", "library"]
    assert not list(data.glob("*.sqlite3"))
