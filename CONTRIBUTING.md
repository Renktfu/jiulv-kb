# 参与贡献

## 跑起来

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[parsers,dev]"
pytest --basetemp=.pytest-tmp
```

`--basetemp` 只是为了避免部分沙箱环境写系统临时目录被拦，普通环境直接 `pytest` 也行。

## 不做什么

不把真实资料塞进仓库。`.gitignore` 已经挡住 `corpus/`、`config.yaml`、`data/*.sqlite3`。测试用的是 `tests/` 里手写的小语料，不依赖任何真实数据。

不在核心链路上加重依赖。fastapi、uvicorn、pyyaml 是底线，docx／pdf／xlsx 解析和向量检索都必须是可选依赖，缺了要能优雅降级。

不为了排序效果去调参调到过拟合。改动排序逻辑请附一个能让别人复现的查询例子。

## 值得做的事

新增解析格式。现在支持 md／txt／docx／pdf／xlsx，pptx、csv 表格结构、带 OCR 的图片都还没做。加格式只需在 `jiulv/parsers.py` 里加一个分支，并补 `SUPPORTED_EXTS`。

改进中文排序。同义词（三亚／Sanya）、近义痛点（选房／房型／朝向）现在只能靠二元组撞命中，加一层同义词扩展会有明显提升。

钩子类型体系。现在的六类是从已有条目反推的，如果你有更细的分类，改 `taxonomy.yaml` 就能生效，也可以提 PR 讨论默认值。

检索结果的可解释性。想加「为什么这条排前面」的分数拆解，对使用者理解结果有帮助。

前端。现在是零构建的原生 JS，功能完整但不支持结果批量收藏、对比阅读、导出 Markdown。加这些不需要引入框架，保持零构建更好。

## 提 PR 之前

跑一遍测试，确认全绿。

如果是行为变更，顺手更新 `docs/` 里对应的说明。文档和代码不一致比没文档更糟。

commit message 说清楚改了什么、为什么改。一句话够了，不用写成长文。

## 报问题

带上这几样，能省很多来回：

`jiulv --version`、出问题的查询或请求体、期望结果与实际结果、`jiulv stats` 的输出。

收录相关的问题再加一份 `jiulv ingest -v` 的日志，里面有每篇文件的处理结果。
