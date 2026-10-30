"""启动前的自举：这个 .venv 到底是不是本机的。

分发包被人在原处双击过一次，46MB 的 .venv 就跟着包一起拷给了别人。里面的
bin/python 是软链、pyvenv.cfg 的 home 指向前一台机器的解释器、site-packages
按旧路径找。启动器只判断「文件在不在」，于是照样拿它去跑，结果是窗口闪一下
就没了，或者一片 ModuleNotFoundError——用户完全猜不到要重建虚拟环境。

所以判据不能是「存在」，得是「它自己说 sys.prefix 在哪」。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    """scripts/ 不是包，按路径加载。"""
    path = ROOT / "scripts" / "setup.py"
    spec = importlib.util.spec_from_file_location("_setup_boot", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_setup_boot"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


setup = _load()


def test_plain_symlinked_python_is_rejected(tmp_path: Path):
    """别的目录的 python 摆进 .venv/bin/python，不能当成本机的虚拟环境。

    这正是「包被拷走」的形状：路径对得上，解释器却是外面那个。
    """
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    fake = bin_dir / "python"
    os.symlink(sys.executable, fake)
    assert fake.exists()
    assert setup.venv_is_usable(fake, tmp_path / ".venv") is False


def test_real_venv_is_accepted(tmp_path: Path):
    """真的在这个目录建出来的 venv 要认。"""
    venv = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
    py = setup.venv_python(venv)
    assert setup.venv_is_usable(py, venv) is True


def test_missing_interpreter_is_rejected(tmp_path: Path):
    """文件都不在，直接 False，不要试着去跑它。"""
    assert setup.venv_is_usable(tmp_path / ".venv" / "bin" / "python", tmp_path / ".venv") is False
