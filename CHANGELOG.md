# Changelog

## [2026-11-18]

### Added
- `schema.sql`：MySQL 表结构（主键、字段类型、注释）。
- `import_data.py`：数据质量校验、主键去重、`INSERT ... ON DUPLICATE KEY UPDATE` 增量 upsert、`--dry-run`。
- `tests/`：数据质量、PMI 对齐、回测输出一致性、加载器校验（标准库 `unittest`，无需 pytest）。
- `scripts/make_dashboard_preview.py` + `figures/hs300_dashboard.png`：看板静态预览。
- `docs/design.md`：关键设计取舍与开发时间线。
- `run_all.py`：一键按顺序运行 01–04。
- `LICENSE`（MIT）。

### Changed
- `requirements.txt` 锁定本机验证版本。
- `.gitignore` 补充 `.env.*`、日志、缓存、系统文件。
- README 增加 MySQL 表结构、数据字典、数据质量校验、测试、复现环境与看板预览。

## [2026-11-17]

### Added
- 特征工程：趋势、动量、波动、量能、宏观五类特征；相关性诊断 + VIF 复核。
- IC/ICIR 检验：Pearson/Spearman、120 日滚动 IC、1/5/20/60 日非重叠抽样。
- z-band 回测：252 日滚动 z-score、滞回带、单边 5bp 成本、70/30 样本内外、参数敏感性。
- Power BI 交互式看板 `figures/hs300_dashboard.pbix`。
- README：结果表、方法细节、已知限制。

## [2026-11-11]

### Added
- 初始化仓库。
- `01_fetch_data.py`：沪深300日线获取、日收益与净值。
- `02_align_pmi.py`：PMI 月频数据 backward 对齐。
