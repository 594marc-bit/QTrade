## MODIFIED Requirements

### Requirement: 数据源可配置
系统 SHALL 通过 `config.ini` 文件中的 `data_source` 配置项选择使用 AKShare 或 Tushare 作为数据获取源，默认值为 `akshare`。

#### Scenario: 使用 AKShare 数据源
- **WHEN** config.ini 中 data_source = akshare（或未配置）
- **THEN** 系统 import AKShare fetcher 模块获取数据

#### Scenario: 使用 Tushare 数据源
- **WHEN** config.ini 中 data_source = tushare
- **THEN** 系统 import Tushare fetcher 模块获取数据

#### Scenario: 配置了不支持的数据源
- **WHEN** config.ini 中 data_source = unknown_source
- **THEN** 系统启动时报错，提示支持的数据源列表

## ADDED Requirements

### Requirement: Tushare Token 配置管理
系统 SHALL 从 `config.ini` 文件的 `[tushare]` 段读取 `token` 配置，并在 `config.ini.example` 中提供模板。

#### Scenario: 从配置文件读取 token
- **WHEN** 数据源配置为 tushare
- **THEN** 系统从 config.ini 的 [tushare] 段读取 token 并初始化 Tushare pro_api

#### Scenario: config.ini 不存在
- **WHEN** config.ini 文件不存在
- **THEN** 系统使用默认配置（akshare 数据源），不报错

### Requirement: 配置文件模板
项目 SHALL 包含 `config.ini.example` 模板文件，展示所有可配置项及说明。`config.ini` SHALL 被 `.gitignore` 排除。

#### Scenario: 新用户配置
- **WHEN** 新用户克隆项目后
- **THEN** 可复制 config.ini.example 为 config.ini，填入自己的 Tushare token 即可使用

### Requirement: fetcher 统一调度接口
`fetcher.py` SHALL 作为统一调度层，根据 config 中的数据源配置，将请求分发到对应的 fetcher 实现，对外接口保持不变。

#### Scenario: 调度层透明切换
- **WHEN** main.py 调用 `from src.data.fetcher import get_index_constituents, get_stocks_batch`
- **THEN** 无论底层使用 AKShare 还是 Tushare，函数签名和返回格式完全一致
