# QTrade

多因子量化选股系统，支持沪深300、中证500、中证1000成分股的因子分析与回测。
- Vibe Coding - Spec Driven
- 主要用沪深300测试，其它的可以自己去完善
- 如果赚钱了记得回来点下星星，如果亏了就当无事发生

## 功能
因为是Vibe Coding 所以就Vibe Test了一下，有问题你可以自己修改，不用提交issue我不会修复的
- **数据获取** — 支持 AKShare（免费）和 Tushare 两种数据源，自动增量同步。（AKShare会达到上限被封IP，Tushare可能需要充钱）
- **因子计算** — 动量、波动率、RSI、换手率、估值（PE/PB）等 10+ 因子，可自由组合（因子不够自己加）
- **IC 分析** — 自动评估因子预测能力（IC 均值、ICIR、胜率）
- **综合评分** — 可配置因子权重，支持自适应权重
- **回测引擎** — 月/周/季频调仓，T+1 执行，避免前视偏差
- **风控** — 个股止损/止盈、组合最大回撤止损、冷冻期
- **仓位管理** — 等权 / 评分加权 / 风险平价
- **行业中性** — 可选约束单行业持仓上限
- **可视化** — 自动生成因子分布、IC 衰减、回测净值等图表

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置

```bash
# 复制配置模板
cp config.ini.example config.ini

# 如使用 Tushare，配置 API Token
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN=your_token
```

### 运行

**交互式向导（推荐新手）：**

```bash
python run.py
```

**命令行模式：**
（没有详细测试，就大概跑了下，想着以后可以给Agent执行，所以增加了这个功能）
```bash
python run.py \
  --index 000300 \
  --start 20230101 \
  --end 20260411 \
  --source akshare \
  --factors intraday_range_10d,pb_rank,pe_ttm_rank,trend_60d,volatility_20d \
  --capital 1000000 \
  --top-n 20 \
  --rebalance M
```

启用风控：

```bash
python run.py \
  --index 000300 --start 20230101 --end 20260411 --source akshare \
  --factors intraday_range_10d,pb_rank,pe_ttm_rank,trend_60d,volatility_20d \
  --capital 1000000 --top-n 20 --rebalance M \
  --stop-loss -0.08 --take-profit 0.15 --max-drawdown -0.10 --cooldown-days 5
```

## 项目结构

```
QTrade/
├── run.py                  # 入口：交互式向导 / 命令行模式
├── main.py                 # 非交互式批量入口
├── config.ini.example      # 配置模板
├── requirements.txt
├── src/
│   ├── config.py           # 全局配置
│   ├── cli/                # 交互式向导
│   │   ├── wizard.py       # 向导流程与管道
│   │   ├── prompts.py      # 交互式输入
│   │   └── display.py      # Rich 终端输出
│   ├── data/               # 数据层
│   │   ├── akshare_fetcher.py
│   │   ├── tushare_fetcher.py
│   │   ├── fetcher.py      # 数据源路由
│   │   ├── cleaner.py      # 数据清洗
│   │   ├── storage.py      # SQLite 存储
│   │   └── industry.py     # 行业分类
│   ├── factors/            # 因子模块
│   │   ├── base.py         # 因子注册基类
│   │   ├── scorer.py       # 综合评分
│   │   ├── ic_analyzer.py  # IC 分析
│   │   ├── adaptive_weights.py
│   │   ├── industry_neutral.py
│   │   ├── momentum.py     # 动量因子
│   │   ├── volatility.py   # 波动率因子
│   │   ├── rsi.py          # RSI 因子
│   │   ├── volume.py       # 成交量因子
│   │   ├── turnover.py     # 换手率因子
│   │   ├── ma_deviation.py # 均线偏离因子
│   │   ├── intraday_range.py # 日内振幅因子
│   │   ├── valuation.py    # 估值因子（PE/PB）
│   │   ├── return_20d.py   # 20日收益因子
│   │   └── trend_60d.py    # 60日趋势因子
│   ├── backtest/           # 回测引擎
│   │   ├── engine.py       # 回测主循环
│   │   ├── portfolio.py    # 持仓管理
│   │   ├── metrics.py      # 绩效指标
│   │   ├── position_sizing.py
│   │   └── result.py
│   └── visualization/      # 图表生成
│       ├── charts.py       # 因子分析图表
│       └── backtest_charts.py
├── tests/
│   ├── test_factors.py
│   └── test_strategy_risk.py
└── data/                   # 数据缓存（自动生成）
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--index` | 股票池：000300 / 000905 / 000852 | 000300 |
| `--start` | 数据起始日期 (YYYYMMDD) | 20230101 |
| `--end` | 数据结束日期 (YYYYMMDD) | 当天 |
| `--source` | 数据源：akshare / tushare | akshare |
| `--factors` | 启用因子，逗号分隔 | 5 个默认因子 |
| `--capital` | 初始资金 | 1000000 |
| `--top-n` | 持仓数量 | 20 |
| `--rebalance` | 调仓频率：M / W / Q | M |
| `--position-sizing` | 仓位管理：equal_weight / score_weighted / risk_parity | equal_weight |
| `--stop-loss` | 个股止损阈值 | -0.08 |
| `--take-profit` | 个股止盈阈值 | 0.15 |
| `--max-drawdown` | 组合回撤止损 | -0.10 |
| `--cooldown-days` | 止损后冷冻天数 | 5 |
| `--industry-neutral` | 启用行业中性约束 | 关闭 |
| `--max-industry-pct` | 单行业持仓上限 | 0.30 |
| `--backtest-start` | 回测起始日期 | 使用数据全量 |
| `--backtest-end` | 回测结束日期 | 使用数据全量 |

## 注意事项

- AKShare 免费但有频率限制，大批量抓取时需耐心等待
- Tushare 需要注册获取 Token，免费账户有每分钟请求限制
- 回测使用 T+1 执行（信号日次日开盘价），避免前视偏差
- 风控参数通过 `--stop-loss` 等传入时自动启用风控
