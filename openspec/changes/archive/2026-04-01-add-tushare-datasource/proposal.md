## Why

当前项目仅使用 AKShare 获取数据，但东方财富 API 对频繁请求会封禁 IP（300只股票批量获取即被限制）。需要引入 Tushare 作为备选数据源，用户可在配置中切换，避免单点依赖。同时需要一个独立的配置文件来安全管理 Tushare API Token（不应硬编码在代码中）。

## What Changes

- 新增 Tushare 数据获取模块，实现与 AKShare 相同的接口（获取成分股列表、单只股票日线、批量获取、增量更新）
- 在 `src/config.py` 中增加数据源切换配置（`DATA_SOURCE`）
- 新增 `config.ini` 配置文件用于存放 Tushare API Token（加入 .gitignore）
- 重构现有 `fetcher.py`，使用数据源抽象层根据配置动态选择 AKShare 或 Tushare
- `main.py` 适配新的数据源切换机制

## Capabilities

### New Capabilities

- `tushare-fetcher`: Tushare 数据获取模块，实现与 AKShare 相同的数据接口（成分股列表、日线行情、批量获取、增量更新），使用 tushare pro_api 接口

### Modified Capabilities

- `data-pipeline`: 增加数据源配置项（`DATA_SOURCE`）和 Tushare token 管理文件，fetcher 根据 config 动态选择数据源

## Impact

- **新增依赖**: `tushare` Python 包
- **新增文件**: `src/data/tushare_fetcher.py`、`config.ini`（token 配置文件）
- **修改文件**: `src/config.py`（增加数据源配置）、`src/data/fetcher.py`（重构为统一调度层）
- **.gitignore**: 添加 `config.ini` 防止 token 泄露
