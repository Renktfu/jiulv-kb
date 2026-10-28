#!/bin/bash
# macOS 双击启动。Finder 里双击这个文件就会开终端、起服务、开浏览器。
# 想放 Dock：拖进 Dock 即可（会变成终端图标）。

cd "$(dirname "$0")/.." || exit 1

# 解释器的挑选顺序：项目虚拟环境 → 系统 python3。
# 关键是「挑完要验一下依赖在不在」：双击启动时的 PATH 比终端里窄，
# 很多人「终端能跑、双击报 ModuleNotFoundError」，就是挑到了另一个 python。
pick_python() {
  local cand
  for cand in "./.venv/bin/python" "./.venv/bin/python3"; do
    if [ -x "$cand" ] && "$cand" -c "import yaml, fastapi, uvicorn" >/dev/null 2>&1; then
      printf '%s' "$(pwd)/${cand#./}"
      return 0
    fi
  done
  for cand in "$(command -v python3)" "/opt/homebrew/bin/python3" "/usr/local/bin/python3"; do
    if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c "import yaml, fastapi, uvicorn" >/dev/null 2>&1; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "没找到装好依赖的 Python。在项目目录里执行这两句："
  echo
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/pip install -e \".[parsers]\""
  echo
  echo "装完再双击这个文件。诊断环境：.venv/bin/python -m jiulv.cli doctor"
  echo "按回车关闭。"
  read -r _
  exit 1
fi

"$PY" -m jiulv.cli start "$@"

# 异常退出时把窗口留住，否则报错一闪而过等于没报。
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "退出码 $status。排查先跑：$PY -m jiulv.cli doctor"
  echo "按回车关闭。"
  read -r _
fi
exit $status
