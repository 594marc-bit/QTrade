## Context

QTrade 当前已实现数据获取、清洗、因子计算和 IC 分析，但结果仅以文本形式输出到终端。用户无法直观地看到因子的时序表现、评分分布和排名情况。需要一个可视化模块将分析结果转为图表。

当前因子系统包含三个因子：momentum_20d、vol_ratio、volatility_20d，通过 cross-sectional Z-Score 标准化后加权求和得到 total_score。IC 分析基于 Spearman 相关系数。

## Goals / Non-Goals

**Goals:**
- 生成 4 类关键图表：IC 时序图、评分分布直方图、Top 10 排名条形图、因子相关性热力图
- 图表自动保存为 PNG 文件
- 与现有 main.py 管道无缝集成

**Non-Goals:**
- 不做交互式仪表盘（Dash/Streamlit）
- 不做实时行情图表
- 不做网页端展示

## Decisions

### 使用 matplotlib 作为图表库

**选择**: matplotlib
**备选**: plotly（交互式但需要浏览器）、pyecharts（功能多但重）

**理由**: 项目为本地分析工具，matplotlib 轻量、无额外服务依赖、PNG 输出稳定。

### 图表模块独立

创建 `src/visualization/charts.py` 模块，不修改现有因子/数据模块。每个图表对应一个函数，由 `main.py` 在管道末尾调用。

### 中文字体支持

macOS 默认 matplotlib 不支持中文，需要显式设置字体（SimHei 或 PingFang SC）。在图表模块中统一配置。

## Risks / Trade-offs

- **[中文字体缺失]** → 按平台自动选择字体，macOS 用 PingFang SC，Linux 用 SimHei，fallback 到英文
- **[图表文件累积]** → 每次运行覆盖同名文件，不保留历史版本
