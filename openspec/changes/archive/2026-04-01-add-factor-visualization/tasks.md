## 1. 依赖与目录

- [x] 1.1 在 `requirements.txt` 中添加 `matplotlib`
- [x] 1.2 创建 `src/visualization/__init__.py` 和 `data/charts/` 目录

## 2. 图表模块

- [x] 2.1 创建 `src/visualization/charts.py` — 中文字体配置和公共样式设置
- [x] 2.2 实现 `plot_ic_timeseries()` — 因子 IC 时序折线图
- [x] 2.3 实现 `plot_score_distribution()` — 综合评分分布直方图
- [x] 2.4 实现 `plot_top10_stocks()` — Top 10 股票水平条形图（带股票名称和分项贡献）
- [x] 2.5 实现 `plot_factor_correlation()` — 因子相关性热力图
- [x] 2.6 实现 `generate_all_charts()` — 统一调用入口

## 3. 管道集成

- [x] 3.1 修改 `main.py` — 在管道末尾添加可视化步骤，调用 `generate_all_charts()`
- [x] 3.2 修改 `src/data/storage.py` 的 `export_csv` — 导出包含因子得分列的数据
