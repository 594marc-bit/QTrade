## Context

QTrade 当前仅通过 AKShare（底层调用东方财富 API）获取 A 股日线数据。东方财富对频繁请求有严格限频，300 只股票批量获取即触发 IP 封禁。项目需要一个备选数据源以提高可用性。

当前架构：
- `src/data/fetcher.py` — 直接调用 AKShare，硬编码绑定单一数据源
- `src/config.py` — 配置项硬编码，无外部配置文件
- `main.py` — 直接 import fetcher 函数

## Goals / Non-Goals

**Goals:**
- 支持 Tushare 作为备选数据源，与 AKShare 保持相同的数据接口
- 通过配置文件切换数据源，无需修改业务代码
- Tushare API Token 通过独立配置文件管理，不硬编码在代码中
- 保持现有 AKShare 功能不受影响

**Non-Goals:**
- 不做实时行情推送（仅日线级别）
- 不做多数据源并行获取或自动切换
- 不重构因子计算和清洗模块（数据格式保持一致即可）

## Decisions

### 1. 配置文件格式：INI 文件

**选择**: 使用 `config.ini`（Python 标准库 configparser 直接支持）
**原因**: 只需要存放少量配置（Tushare token、数据源选择），INI 足够简单，无需引入 YAML/TOML 额外依赖。
**备选**: `.env` 文件 — 也可以，但 INI 支持分组更清晰。

### 2. 数据源切换方式：config.py 中的 DATA_SOURCE 常量

**选择**: 在 `src/config.py` 中增加 `DATA_SOURCE = "akshare"` 或 `"tushare"`，从 `config.ini` 读取
**原因**: 简单直接，所有模块都从 config.py 导入配置，改动最小。
**备选**: 工厂模式 + 注册表 — 对于只有 2 个数据源来说过度设计。

### 3. Tushare 接口选择：pro_api

**选择**: 使用 `tushare.pro_api()` 接口
**原因**: Tushare Pro 是当前主力接口，数据质量高、更新及时，支持前复权。老接口已逐步废弃。
**关键接口**:
- 成分股: `pro.index_weight()` 或 `pro.index_cons()`
- 日线行情: `pro.daily()` + `pro.adj_factor()`（需分开获取再合并计算复权价）
- 前复权: Tushare 不直接提供前复权价，需要用 `adj_factor` 自行计算

### 4. 前复权实现：本地计算

**选择**: 获取原始日线 `pro.daily()` + 复权因子 `pro.adj_factor()`，本地计算前复权价
**计算公式**: `前复权价 = 原始价 × 当日复权因子 / 最新复权因子`
**原因**: Tushare 不像 AKShare 直接返回复权数据，需要自行处理。
**备选**: 使用 `ts.pro_bar()` 接口（支持直接返回复权数据）— 但该接口较慢，且部分用户反馈不稳定。

### 5. fetcher.py 重构：调度层

**选择**: 将现有 `fetcher.py` 重命名为 `akshare_fetcher.py`，新建 `tushare_fetcher.py`，`fetcher.py` 变为统一调度层
**原因**: 保持对外接口不变（`main.py` 只需 import fetcher），内部根据 `DATA_SOURCE` 配置分发到具体实现。
**备选**: 继承抽象基类 — 当前只需要简单的 if/else 分发，不必过度抽象。

## Risks / Trade-offs

- **Tushare 积分限制** → 新用户只有 120 分/分钟调用频率，需在 config 中配置合理的请求间隔；大盘数据（如成分股列表）可能需要更高积分
- **Tushare token 泄露风险** → `config.ini` 加入 `.gitignore`，并提供 `config.ini.example` 模板
- **两个数据源的输出格式可能有微小差异** → 统一在各自的 fetcher 中做列名映射和数据类型转换，确保输出格式完全一致
