#coding:gbk
"""
QMT 策略：5分钟K线数据导出

功能：
  将沪深A股全市场5分钟K线数据（前复权）增量导出到本地 SQLite，
  供 qmt_api_server.py 提供 HTTP API，供 Mac 端网格回测等策略使用。

表名: kline_5m（与 daily_price 在同一数据库文件中）
  bar_time TEXT 'YYYYMMDDHHMMSS'
  ts_code  TEXT '000001.SZ'
  open/high/low/close REAL 前复权价（元）
  vol      REAL 成交量（手，QMT volume 直用）
  amount   REAL 成交额（千元，QMT amount 为元，写入前 /1000）
  is_trading REAL 1.0=交易 0.0=停牌（按 amount>0 判断）

使用步骤：
  1. QMT 客户端：操作 -> 数据管理 -> 下载 5分钟线/沪深A股/全部历史，并勾选定时下载
  2. 新建策略，粘贴本文件，挂任意主图运行（回测/模拟均可，不下单）
  3. 首次运行自动全量导出（20180101至今，数据量大，建议仅导出近2年），之后每天15:35后增量
  4. 如需全量重建：将 FULL_REFRESH 改为 True 重新运行一次，完成后改回 False

注意事项：
  - 第一行 #coding:gbk 为 QMT 强制要求
  - 全局状态使用自定义类实例，不要挂在 ContextInfo 上（bar回滚会导致值被重置）
  - run_time 定时任务是一次性的，回调末尾需重新注册（见 sync_task）
  - 5分钟数据量约为日线的48倍，全量导出（2018至今全市场）约需数小时，建议将 START_DATE 设为近2年
  - 本策略只读行情写本地库，不做任何交易
"""

import os
import sqlite3
import datetime

# ============================================================
# 全局状态（QMT要求：不直接挂在ContextInfo上，用全局类实例）
# ============================================================

class G:
    """全局状态容器"""
    # --- 配置参数 ---
    DB_PATH = r'C:\quant_data\stock_data.db'    # 与日线导出同一数据库文件
    TABLE_NAME = 'kline_5m'                     # 表名
    START_DATE = '20240101'                     # 全量导出起始日期（5m数据量大，建议近2年）
    FULL_REFRESH = False                        # True=清表全量重建
    BATCH_SIZE = 50                             # 每批处理的股票数（5m数据量大，减小批次）
    SYNC_AFTER = '153500'                       # 每天此时间(HHMMSS)后执行当日增量同步

    # --- 运行状态 ---
    stock_list = []                             # 沪深A股代码列表
    last_sync_date = None                       # 当天已完成同步的日期 'YYYYMMDD'


g = G()

# ============================================================
# 初始化
# ============================================================

def init(ContextInfo):
    """策略加载时执行：建表 -> 获取股票列表 -> 立即同步一次 -> 注册定时任务"""
    _init_db()

    g.stock_list = ContextInfo.get_stock_list_in_sector('沪深A股')
    print(f"[init] 沪深A股共 {len(g.stock_list)} 只")
    if not g.stock_list:
        print("[init] 错误：未获取到沪深A股列表，请检查QMT板块数据")
        return

    # 启动即执行一次同步（空表=全量，否则增量）
    _sync_once(ContextInfo)
    g.last_sync_date = datetime.datetime.now().strftime('%Y%m%d')

    # 每小时触发一次检查（run_time 为一次性任务，回调内重新注册）
    ContextInfo.run_time("sync_task", "3600nSecond", "")
    print("[init] 5分钟K线导出策略初始化完成，每小时检查一次，%s 后执行当日增量" % g.SYNC_AFTER)


def handlebar(ContextInfo):
    """本策略不依赖K线驱动，逻辑全部在 run_time 定时任务中"""
    pass


# ============================================================
# 定时任务
# ============================================================

def sync_task(ContextInfo):
    """每小时触发：盘后（SYNC_AFTER 之后）且当天未同步时执行增量同步"""
    try:
        now = datetime.datetime.now()
        today = now.strftime('%Y%m%d')
        if now.strftime('%H%M%S') >= g.SYNC_AFTER and g.last_sync_date != today:
            _sync_once(ContextInfo)
            g.last_sync_date = today
    except Exception as e:
        print(f"[sync_task] 同步失败: {e}")
    finally:
        # run_time 是一次性的，必须重新注册才能循环
        ContextInfo.run_time("sync_task", "3600nSecond", "")


# ============================================================
# 数据导出
# ============================================================

def _init_db():
    """建表建索引，WAL 模式供 API 进程并发读"""
    db_dir = os.path.dirname(g.DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    conn = sqlite3.connect(g.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {g.TABLE_NAME} (
            bar_time TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            vol REAL,
            amount REAL,
            is_trading REAL
        )
    """)
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{g.TABLE_NAME}_unique "
        f"ON {g.TABLE_NAME} (bar_time, ts_code)"
    )
    conn.commit()
    conn.close()


def _sync_once(ContextInfo):
    """执行一次同步：增量起点=库内最新 bar_time 日期部分（当日重拉覆盖，INSERT OR REPLACE 幂等）"""
    conn = sqlite3.connect(g.DB_PATH)
    cursor = conn.cursor()

    if g.FULL_REFRESH:
        print(f"[sync] FULL_REFRESH=True，清空 {g.TABLE_NAME} 全量重建...")
        cursor.execute(f"DELETE FROM {g.TABLE_NAME}")
        conn.commit()

    # 增量起点：取库内最新 bar_time 的前8位（日期部分）
    row = cursor.execute(f"SELECT MAX(bar_time) FROM {g.TABLE_NAME}").fetchone()
    if row and row[0]:
        start = row[0][:8]  # 'YYYYMMDDHHMMSS' -> 'YYYYMMDD'
    else:
        start = g.START_DATE
    end = datetime.datetime.now().strftime('%Y%m%d')
    print(f"[sync] 同步区间 {start} ~ {end}，共 {len(g.stock_list)} 只股票")

    t0 = datetime.datetime.now()
    total = 0
    n_batch = (len(g.stock_list) + g.BATCH_SIZE - 1) // g.BATCH_SIZE

    empty_stocks = []

    for i in range(0, len(g.stock_list), g.BATCH_SIZE):
        batch = g.stock_list[i:i + g.BATCH_SIZE]

        # 先触发下载，确保本地缓存有数据（get_market_data_ex 只读缓存不联网）
        try:
            ContextInfo.download_history_data(
                batch, period='5m',
                start_time=start, end_time=end,
            )
        except Exception:
            pass

        try:
            data = ContextInfo.get_market_data_ex(
                ['open', 'high', 'low', 'close', 'volume', 'amount'],
                batch,
                period='5m',
                start_time=start,
                end_time=end,
                dividend_type='front_ratio',
                fill_data=False,
                subscribe=False,
            )
        except Exception as e:
            print(f"[sync] 批次 {i // g.BATCH_SIZE + 1} 获取失败: {e}")
            continue

        records = []
        for stock in batch:
            df = data.get(stock)
            if df is None or df.empty:
                empty_stocks.append(stock)
                continue
            for idx, bar in df.iterrows():
                bar_time = _to_bar_time(idx, bar)
                if bar_time is None:
                    continue
                try:
                    o = float(bar['open'])
                    h = float(bar['high'])
                    l = float(bar['low'])
                    c = float(bar['close'])
                    v = float(bar['volume'])
                    a = float(bar['amount'])
                except (ValueError, TypeError, KeyError):
                    continue
                if not c > 0:
                    continue
                records.append((
                    bar_time, stock,
                    o, h, l, c,
                    v,                              # 手
                    a / 1000.0,                     # 元 -> 千元
                    1.0 if a > 0 else 0.0,
                ))

        if records:
            cursor.executemany(f"""
                INSERT OR REPLACE INTO {g.TABLE_NAME}
                (bar_time, ts_code, open, high, low, close, vol, amount, is_trading)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            conn.commit()
            total += len(records)
        print(f"[sync] 批次 {i // g.BATCH_SIZE + 1}/{n_batch} 写入 {len(records)} 行，累计 {total}")

    n_stocks = cursor.execute(f"SELECT COUNT(DISTINCT ts_code) FROM {g.TABLE_NAME}").fetchone()[0]
    date_range = cursor.execute(
        f"SELECT MIN(bar_time), MAX(bar_time) FROM {g.TABLE_NAME}"
    ).fetchone()
    conn.close()

    cost = (datetime.datetime.now() - t0).total_seconds()
    print(f"[sync] 完成：本次写入 {total} 行，耗时 {cost:.0f} 秒")
    print(f"[sync] 库内现有 {n_stocks} 只股票，时间范围 {date_range[0]} ~ {date_range[1]}")
    if empty_stocks:
        print(f"[sync] 警告：{len(empty_stocks)} 只股票无数据: {empty_stocks[:10]}...")


def _to_bar_time(idx, bar):
    """K线索引 -> 'YYYYMMDDHHMMSS'

    5m线索引通常是毫秒时间戳或 'YYYYMMDDHHMMSS' 字符串。
    优先当作 datetime 字符串处理，兜底用 timetag_to_datetime。
    """
    date_str = str(idx)
    # 'YYYYMMDDHHMMSS' = 14位
    if len(date_str) >= 14 and date_str[:14].isdigit():
        return date_str[:14]
    # 'YYYYMMDD' = 8位（异常，兜底补零）
    if len(date_str) >= 8 and date_str[:8].isdigit():
        return date_str[:8] + '000000'
    try:
        # 兜底：time 列为毫秒时间戳，timetag_to_datetime 为 QMT 内置函数
        return timetag_to_datetime(int(bar['time']), '%Y%m%d%H%M%S')
    except Exception:
        return None
