## Context

QTrade 是一个 A 股多因子量化选股系统，当前使用 9 个因子（动量、量比、波动率、RSI、MA偏离、换手动量、日内波幅、PE排名、PB排名）按固定权重打分，等权买入 Top N 股票。回测引擎支持日/周/月调仓，有交易成本模型但无风控机制。

现有模块结构：
- `src/factors/scorer.py` — 固定 `DEFAULT_WEIGHTS` 字典，`compute_total_score()` 加权求和
- `src/backtest/engine.py` — `BacktestEngine.run()` 仅在调仓日执行操作
- `src/backtest/portfolio.py` — `Portfolio.rebalance()` 等权分配
- `src/config.py` — 静态配置，无运行时切换能力

## Goals / Non-Goals

**Goals:**
- 因子权重能根据历史 IC 动态调整，提升策略适应性
- 选股结果行业分布合理，避免单一行业过度集中
- 回测引擎在非调仓日也能执行风控（止损/止盈）
- 持仓权重能反映个股评分质量和风险水平

**Non-Goals:**
- 不实现实盘交易接口
- 不引入机器学习/深度学习模型
- 不改变现有因子计算逻辑和 IC 分析框架
- 不做 Walk-forward 或样本外检验（属于回测增强，非本次范围）

## Decisions

### D1: 自适应权重方案 — 滚动 IC 均值加权

**方案**: 取各因子过去 N 个调仓周期（默认 60 交易日）的日 IC 序列，计算 IC 均值作为权重基础，再经过缩放和正负号处理得到最终权重。

**替代方案**: IC_IR（IC 信息比率）、最大化组合 IC、Bayesian 更新。

**理由**: 滚动 IC 均值直观、计算简单、参数少，适合当前系统规模。IC_IR 虽更稳健但初期实现复杂度不匹配。

**接口**: 新增 `src/factors/adaptive_weights.py`，提供 `compute_adaptive_weights(ic_df, window=60)` → `dict[str, float]`。`scorer.py` 的 `compute_total_score()` 增加可选 `weights` 参数，默认仍使用 `DEFAULT_WEIGHTS`。

### D2: 行业中性 — 约束式选股

**方案**: 选股时限制单一行业最多占比 `max_industry_pct`（默认 30%）。在 `select_top_n()` 之后再做一轮行业过滤：按评分排序依次纳入，若某行业已达上限则跳过。

**数据来源**: Tushare 的 `index_classify` 或 AKShare 的行业分类接口，获取沪深300成分股的行业归属，存入 SQLite。

**替代方案**: 行业中性化因子（先做行业哑变量回归取残差）、优化器约束。

**理由**: 约束式选股实现简单、直观，不改变因子计算流程。因子残差化虽更"干净"但会引入回归计算复杂度和多共线性问题。

### D3: 风控检查 — 在回测引擎中每日触发

**方案**: 回测引擎 `run()` 的主循环中，每个交易日（不仅是调仓日）都执行风控检查：
1. 个股止损/止盈：当前持仓中个股收益率触发阈值 → 标记卖出
2. 组合回撤止损：当日组合净值回撤超限 → 清仓全部持仓

**实现**: 在 `Portfolio` 中新增 `check_risk_controls(current_prices)` 方法，返回需卖出的股票列表。`BacktestEngine` 每日调用此方法，触发卖出操作。

**替代方案**: 独立风控模块、事件驱动架构。

**理由**: 在现有循环结构中直接调用最简单，不引入新的事件总线或回调机制。独立风控模块属于过度设计。

### D4: 仓位管理 — 评分加权模式

**方案**: 新增两种仓位模式（通过配置选择）：
1. **score_weighted**: 权重 = 个股评分 / 总评分，高评分股票占比更大
2. **risk_parity**: 权重 ∝ 1/个股波动率，使每个股票的风险贡献相等

`Portfolio.rebalance()` 的 `target_weights` 参数从 `None`（等权）扩展为接受权重字典。

**替代方案**: 等风险贡献（ERC）、均值方差优化（MVO）、Black-Litterman。

**理由**: 评分加权是对现有逻辑的最小扩展；风险平价实现简单且理论基础扎实。MVO 对估计误差敏感，不适合当前规模。

### D5: 配置管理 — config.ini 扩展

**方案**: 在 `config.ini` 新增 `[risk_control]` 和 `[position_sizing]` section，所有新功能通过配置项控制开关和参数。`src/config.py` 读取并暴露为全局变量。默认值保持向后兼容（新功能默认关闭）。

```ini
[risk_control]
enabled = false
stop_loss = -0.08
take_profit = 0.15
max_drawdown_stop = -0.10

[industry_neutral]
enabled = false
max_industry_pct = 0.30

[position_sizing]
method = equal_weight  # equal_weight / score_weighted / risk_parity

[adaptive_weights]
enabled = false
ic_window = 60
```

## Risks / Trade-offs

- **[自适应权重过拟合]** → 使用较长滚动窗口（≥60日），并保留固定权重作为 fallback
- **[行业分类数据不完整/延迟]** → 缓存行业数据到 SQLite，缺失时降级为无约束模式
- **[止损后频繁交易增加成本]** → 止损后设置冷冻期（如 5 个交易日不再买入同一股票）
- **[风险平价极端权重]** → 设置单只股票权重上下限（如 5%~20%）
- **[向后兼容]** → 所有新功能默认关闭（enabled=false），需显式开启才生效
