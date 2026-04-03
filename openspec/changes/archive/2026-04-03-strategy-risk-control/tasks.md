## 1. 配置扩展

- [x] 1.1 在 `config.ini` 新增 `[risk_control]`、`[industry_neutral]`、`[position_sizing]`、`[adaptive_weights]` 四个 section 及默认参数
- [x] 1.2 在 `src/config.py` 中解析新配置项，暴露为全局变量，未配置时使用默认值

## 2. 自适应权重模块

- [x] 2.1 创建 `src/factors/adaptive_weights.py`，实现 `compute_adaptive_weights(ic_df, window=60)` 函数：计算滚动 IC 均值并转换为权重
- [x] 2.2 修改 `src/factors/scorer.py` 的 `compute_total_score()`，增加可选 `weights` 参数，默认使用 `DEFAULT_WEIGHTS`
- [x] 2.3 在 `main.py` 的管道中，当 `adaptive_weights.enabled=true` 时调用自适应权重并传入评分函数

## 3. 行业中性模块

- [x] 3.1 创建 `src/data/industry.py`，实现行业分类数据获取（Tushare/AKShare）和 SQLite 缓存（`industry_classify` 表）
- [x] 3.2 创建 `src/factors/industry_neutral.py`，实现 `apply_industry_constraint(top_stocks, industry_map, max_pct)` 约束式选股函数
- [x] 3.3 修改 `main.py` 选股流程，在 `select_top_n()` 之后调用行业约束过滤
- [x] 3.4 在回测管道中加载行业数据并传递给选股模块

## 4. 风控模块

- [x] 4.1 修改 `src/backtest/portfolio.py`，新增 `check_risk_controls(current_prices, config)` 方法，返回需卖出的股票列表及原因
- [x] 4.2 在 `Portfolio` 中新增止损冷却期（`cooldown` 字典）追踪逻辑
- [x] 4.3 修改 `src/backtest/engine.py` 的 `run()` 主循环，每个交易日调用风控检查，非调仓日也执行卖出
- [x] 4.4 实现组合回撤止损逻辑：追踪历史最高净值，回撤超限时清仓
- [x] 4.5 确保调仓日先执行风控卖出再执行调仓操作

## 5. 仓位管理模块

- [x] 5.1 创建 `src/backtest/position_sizing.py`，实现三种模式：`equal_weight`、`score_weighted`、`risk_parity`
- [x] 5.2 修改 `Portfolio.rebalance()` 支持 `target_weights` 参数（字典），非 None 时按指定权重调仓
- [x] 5.3 实现权重上下限裁剪（`min_weight`/`max_weight`）和剩余权重再分配逻辑
- [x] 5.4 在回测管道中根据 `position_sizing.method` 配置生成权重并传入 `rebalance()`

## 6. 集成与测试

- [x] 6.1 更新 `tests/test_factors.py`，新增自适应权重计算的单元测试（正常计算、数据不足回退）
- [x] 6.2 新增行业中性选股约束的单元测试（正常约束、无行业数据降级）
- [x] 6.3 更新 `tests/test_backtest.py`，新增风控检查测试（止损、止盈、回撤止损、冷却期）
- [x] 6.4 新增仓位管理模块的单元测试（三种模式、权重上下限）
- [x] 6.5 端到端集成测试：使用真实数据运行完整管道，验证所有新功能可开关且默认关闭
