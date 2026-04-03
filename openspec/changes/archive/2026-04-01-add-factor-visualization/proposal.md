## Why

当前系统的因子分析结果仅以文字输出到终端，用户无法直观地看到因子的表现趋势、股票评分分布和 Top 股票走势。添加可视化图表能让用户快速理解因子效果，辅助投资决策。

## What Changes

- 新增因子可视化模块，生成以下图表：
  - 因子 IC 时序图：展示各因子每日 IC 值的变化趋势
  - 综合评分分布直方图：展示最新一期股票评分分布
  - Top 10 股票评分条形图：带股票名称和分项因子贡献
  - 因子相关性热力图：展示因子之间的相关性
- 在 `main.py` 管道中集成可视化步骤，运行后自动生成图表
- 图表保存为 PNG 文件到 `data/charts/` 目录

## Capabilities

### New Capabilities
- `factor-visualization`: 因子分析结果可视化，包括 IC 时序图、评分分布、Top 排名和因子相关性图表

### Modified Capabilities

## Impact

- 新增依赖：`matplotlib`
- 新增目录：`data/charts/`
- 修改 `main.py`：添加可视化步骤
- 修改 `requirements.txt`：添加 matplotlib
