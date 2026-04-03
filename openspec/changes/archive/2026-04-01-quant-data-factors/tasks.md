## 1. 项目初始化

- [x] 1.1 创建项目目录结构（src/data/, src/factors/, tests/, data/）
- [x] 1.2 创建 requirements.txt（pandas, numpy, akshare, scipy, pytest）
- [x] 1.3 创建 src/config.py 配置管理模块（数据目录、日期范围、缓存策略）

## 2. 数据获取模块

- [x] 2.1 实现 src/data/fetcher.py — 获取沪深300成分股列表（AKShare接口 + 7天缓存）
- [x] 2.2 实现 src/data/fetcher.py — 获取单只股票日线行情（开高低收、成交量、成交额，前复权）
- [x] 2.3 实现 src/data/fetcher.py — 批量获取多只股票行情（0.5s间隔、进度条）
- [x] 2.4 实现 src/data/fetcher.py — 增量更新（从本地最新日期开始获取新数据）

## 3. 数据清洗模块

- [x] 3.1 实现 src/data/cleaner.py — 缺失值处理（ffill填充短期缺失，删除连续5日以上缺失）
- [x] 3.2 实现 src/data/cleaner.py — 停牌标记（成交量=0时 is_trading=False）
- [x] 3.3 实现 src/data/cleaner.py — 异常值过滤（涨跌幅±20%限制、量价矛盾过滤）
- [x] 3.4 实现 src/data/cleaner.py — 数据对齐（统一所有股票到同一交易日时间轴）
- [x] 3.5 实现 src/data/cleaner.py — 数据验证（价格>0、涨跌幅合理、每日≥250只股票）

## 4. 数据存储模块

- [x] 4.1 实现 src/data/storage.py — SQLite 存储接口（daily_price 表、追加去重）
- [x] 4.2 实现 src/data/storage.py — 数据读取接口（按股票代码、日期范围查询）
- [x] 4.3 实现 src/data/storage.py — CSV 导出功能

## 5. 因子计算引擎

- [x] 5.1 实现 src/factors/base.py — 因子抽象基类（calculate 接口、因子注册机制）
- [x] 5.2 实现 src/factors/momentum.py — 动量因子（20日收益率，不足20日设为NaN）
- [x] 5.3 实现 src/factors/volume.py — 成交额动量因子（5日/20日成交额比，零值处理）
- [x] 5.4 实现 src/factors/volatility.py — 波动率因子（20日收益率标准差）
- [x] 5.5 实现 src/factors/scorer.py — Z-Score 标准化（按交易日横截面标准化）
- [x] 5.6 实现 src/factors/scorer.py — 综合打分（加权求和，默认动量40%/成交额动量30%/波动率30%取负）
- [x] 5.7 实现 src/factors/scorer.py — 选股排序（Top N、排除停牌股）

## 6. IC 分析

- [x] 6.1 实现 src/factors/ic_analyzer.py — 计算未来N日收益率
- [x] 6.2 实现 src/factors/ic_analyzer.py — 计算单日IC（Spearman相关系数）
- [x] 6.3 实现 src/factors/ic_analyzer.py — 计算IC序列和统计汇总（IC均值、IC标准差、ICIR、胜率）
- [x] 6.4 实现 src/factors/ic_analyzer.py — 因子有效性判定（IC>0.03 且 胜率>55%）

## 7. 入口脚本与集成测试

- [x] 7.1 实现 main.py — 主入口脚本（串联数据获取→清洗→因子计算→IC分析→输出报告）
- [x] 7.2 编写 tests/test_data_pipeline.py — 数据管道单元测试
- [x] 7.3 编写 tests/test_factors.py — 因子计算单元测试
- [x] 7.4 端到端测试：从数据获取到IC分析完整流程验证
