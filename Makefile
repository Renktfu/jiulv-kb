.PHONY: help install connect sync watch start ingest rebuild serve autostart dist embed test fmt clean

help:
	@echo "make install   建虚拟环境并安装（含解析依赖）"
	@echo "make connect   探测 WorkBuddy / OpenCLaw 资料库并接入"
	@echo "make sync      增量同步（指纹没变就跳过）"
	@echo "make watch     常驻监听资料库，两分钟看一次"
	@echo "make ingest    增量收录"
	@echo "make rebuild   清空重建索引"
	@echo "make start     一条命令跑起来：建索引 + 网页 + 后台同步"
	@echo "make serve     只起网页与接口，默认 8787"
	@echo "make autostart 装成开机自启（off 卸载）"
	@echo "make dist      打分成包：dist/macos 与 dist/windows"
	@echo "make embed     生成语义向量（需先配 semantic）"
	@echo "make test      跑测试"
	@echo "make clean     删掉索引和缓存"

install:
	python3 -m venv .venv
	# setuptools 也得升：3.9 的 venv 自带 58，不支持 pyproject.toml 的 editable 安装，
	# 会报一句完全看不出原因的「File "setup.py" not found」
	.venv/bin/python -m pip install -U pip setuptools wheel
	.venv/bin/python -m pip install -e ".[parsers,dev]"

connect:
	.venv/bin/jiulv connect --dry-run
	@echo "确认无误后跑：.venv/bin/jiulv connect && .venv/bin/jiulv sync"

sync:
	.venv/bin/jiulv sync

watch:
	.venv/bin/jiulv watch

ingest:
	.venv/bin/jiulv ingest

rebuild:
	.venv/bin/jiulv ingest --rebuild

start:
	.venv/bin/jiulv start

serve:
	.venv/bin/jiulv serve

autostart:
	.venv/bin/jiulv autostart status

dist:
	python3 scripts/make_dist.py --clean
	@echo "打好了。拷 dist/macos 或 dist/windows 走，两选一。"

embed:
	.venv/bin/jiulv embed

test:
	.venv/bin/python -m pytest -q --basetemp=.pytest-tmp

clean:
	rm -rf data/kb.sqlite3 data/kb.sqlite3-* data/sync_state.json .pytest-tmp .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
