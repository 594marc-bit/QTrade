## 1. 配置文件

- [x] 1.1 创建 `config.ini.example` 模板文件（包含 data_source 和 tushare token 配置项及说明）
- [x] 1.2 在 `.gitignore` 中添加 `config.ini`
- [x] 1.3 修改 `src/config.py` — 增加 config.ini 读取逻辑，新增 `DATA_SOURCE` 和 `TUSHARE_TOKEN` 配置项

## 2. Tushare 数据获取模块

- [x] 2.1 创建 `src/data/tushare_fetcher.py` — 初始化 Tushare pro_api 连接（token 校验）
- [x] 2.2 实现 `get_index_constituents()` — 通过 `pro.index_cons()` 获取沪深300成分股，输出格式与 AKShare 一致
- [x] 2.3 实现 `get_stock_daily()` — 通过 `pro.daily()` + `pro.adj_factor()` 获取日线行情，本地计算前复权价
- [x] 2.4 实现 `get_stocks_batch()` — 批量获取（0.5s 间隔、进度条、连续失败停止、定期保存）
- [x] 2.5 实现 `get_stock_daily_incremental()` — 增量更新

## 3. Fetcher 调度层重构

- [x] 3.1 将现有 `src/data/fetcher.py` 重命名为 `src/data/akshare_fetcher.py`
- [x] 3.2 创建新的 `src/data/fetcher.py` — 统一调度层，根据 DATA_SOURCE 配置分发到 akshare_fetcher 或 tushare_fetcher
- [x] 3.3 确保 main.py 无需修改（对外接口不变）

## 4. 依赖与测试

- [x] 4.1 在 `requirements.txt` 中添加 `tushare`
- [x] 4.2 编写 `tests/test_tushare_fetcher.py` — Tushare fetcher 单元测试
- [x] 4.3 验证 main.py 可通过修改 config.ini 在两个数据源之间切换
