#!/bin/bash
# 双击启动。Finder 里双击它就会开终端、装好依赖（第一次）、起服务、开浏览器。
# 想放 Dock：拖进 Dock 即可（会变成终端图标）。
#
# 这个文件本身不干正事，只做三件：定位项目目录、找一个能用的 python、交给
# setup.py。装依赖、建索引、起服务、开浏览器全在 setup.py / jiulv 里，
# Windows 那边共用同一份逻辑，改行为不用改两个地方。

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
# 两个位置都要能跑：放在 scripts/ 下时项目在上一级，直接放在包根目录时就是自己。
ROOT="$HERE"
[ -d "$ROOT/jiulv" ] || ROOT="$(cd "$HERE/.." && pwd)"

cd "$ROOT" || {
  echo "进不去项目目录：$ROOT"
  echo "按回车关闭。"
  read -r _
  exit 1
}

PY="$(command -v python3 || true)"
[ -z "$PY" ] && [ -x /opt/homebrew/bin/python3 ] && PY=/opt/homebrew/bin/python3
[ -z "$PY" ] && [ -x /usr/local/bin/python3 ] && PY=/usr/local/bin/python3

if [ -z "$PY" ]; then
  echo "找不到 python3。macOS 上先装一个："
  echo
  echo "  brew install python@3.12"
  echo
  echo "或者去 https://www.python.org/downloads/ 下安装包。装完再双击这个文件。"
  echo "按回车关闭。"
  read -r _
  exit 1
fi

# 交给自举脚本：它负责建 .venv、装依赖，然后把参数原样给 jiulv start。
# 已有环境时会先做一次快速校验，几毫秒就过，不会每次都重新装。
"$PY" scripts/setup.py "$@"
status=$?

# 异常退出时把窗口留住，否则报错一闪而过等于没报。
if [ $status -ne 0 ]; then
  echo
  echo "退出码 ${status}。诊断环境："
  echo "  $PY scripts/setup.py --check"
  echo "  $PY -m jiulv.cli doctor"
  echo "按回车关闭。"
  read -r _
fi
exit $status
