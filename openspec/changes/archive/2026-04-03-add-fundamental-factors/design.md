## Context

当前 QTrade 的因子全部基于量价数据。需要增加基本面因子提供新信号维度。

数据源选择：
- **Tushare `daily_basic`**：每日每只股票返回 PE_TTM、PB、PS_TTM、市值等 18 个字段。需要 2000+ 积分，每次最多 6000 行。
- **AKShare `stock_a_indicator_lg`**：按单只股票获取全历史估值序列（PE、PB、PS、市值），免费无需 token。

当前系统已有双源切换机制（`DATA_SOURCE = "akshare" | "tushare"`），基本面数据复用同一模式。

存储：当前只有 `daily_price` 一张表。基本面数据使用独立的 `daily_basic` 表，避免与行情数据混淆。

## Goals / Non-Goals

**Goals:**
- 新增基本面数据获取（支持 Tushare 和 AKShare 双源）
- 新增 `daily_basic` 存储表（SQLite）
- 新增 2 个估值因子：PE_TTM 排名、PB 排名（截面百分位）
- 集成到现有 pipeline

**Non-Goals:**
- 不实现 ROE/财务指标因子（季度频率数据，与日频量价因子对齐复杂，后续独立 change）
- 不实现 IC 动态加权
- 不修改已有因子逻辑

## Decisions

### 1. 因子计算方式：截面百分位排名

**方案 A（采用）**：每日对所有股票的 PE_TTM/PB 做 cross-sectional percentile ranking，输出 0-100 的百分位值。
- 优点：天然标准化，不受极端值影响，与 Z-score 打分兼容
- 低 PE/PB 的股票排名低（百分位低），配合负权重即可实现"买低估值"

**方案 B**：直接使用原始 PE/PB 值 — 极端值多（银行 PE=4 vs 科技 PE=100），Z-score 后方差大

### 2. 数据获取策略

**Tushare**：按 `trade_date` 批量获取（一次获取全市场某日数据），每次 6000 行上限，足够覆盖 CSI 300。
**AKShare**：按 `ts_code` 逐只获取全历史数据，需循环 300 次（与现有行情获取模式一致）。

新函数：
- `fetch_daily_basic(ts_codes, start_date, end_date)` — 统一接口，内部按 DATA_SOURCE 分发
- `save_daily_basic(df)` / `load_daily_basic(start_date, end_date)` — 存储层

### 3. 因子设计

```python
@register_factor
class PeFactor(FactorBase):
    factor_name = "pe_ttm_rank"
    description = "Cross-sectional percentile rank of PE_TTM"

    def calculate(self, df):
        # df must already have pe_ttm column from daily_basic merge
        df["pe_ttm_rank"] = df.groupby("trade_date")["pe_ttm"].rank(pct=True) * 100
        return df
```

- 因子值 = 0-100 百分位（低值 = 低估值）
- 配合负权重：负权重 × 低百分位 = 正贡献 → 低估值股得分高
- 需要在 pipeline 中先 merge 行情数据和基本面数据，再计算因子

### 4. Pipeline 修改

在 `main.py` 的 pipeline 中，Step 2（清洗后）增加一步：
1. 获取/同步基本面数据 → `daily_basic` 表
2. 将基本面数据 merge 到主 df（left join on trade_date + ts_code）
3. 然后进入因子计算步骤

## Risks / Trade-offs

- **Tushare 积分要求**：`daily_basic` 需要 2000+ 积分 → 部分用户可能无权限。AKShare 备选方案可覆盖。
- **数据延迟**：PE_TTM 每日更新（行情价格变化导致 PE 变化），但底层财务数据按季更新 → 实际上 PE_TTM 每日变化仅反映股价波动。
- **缺失值**：亏损公司 PE 为 NaN → 百分位排名时自动排除，不影响其他股票。
- **AKShare 速率限制**：300 只股票需逐只获取，约 10 分钟（2 秒间隔）→ 可接受，且有缓存机制。
