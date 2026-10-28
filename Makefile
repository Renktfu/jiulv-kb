.PHONY: help install ingest rebuild serve embed test fmt clean

help:
	@echo "make install   建虚拟环境并安装（含解析依赖）"
	@echo "make ingest    增量收录"
	@echo "make rebuild   清空重建索引"
	@echo "make serve     启动网页与接口，默认 8787"
	@echo "make embed     生成语义向量（需先配 semantic）"
	@echo "make test      跑测试"
	@echo "make clean     删掉索引和缓存"

install:
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -e ".[parsers,dev]"

ingest:
	.venv/bin/jiulv ingest

rebuild:
	.venv/bin/jiulv ingest --rebuild

serve:
	.venv/bin/jiulv serve

embed:
	.venv/bin/jiulv embed

test:
	.venv/bin/python -m pytest -q --basetemp=.pytest-tmp

clean:
	rm -rf data/kb.sqlite3 data/kb.sqlite3-* .pytest-tmp .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
