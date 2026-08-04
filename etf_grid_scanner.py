#!/usr/bin/env python3
"""
ETF 网格交易扫描器 — 从 ETF 全市场中找到最适合网格交易的标的，并用 5 分钟 K 线回测

核心流程:
  1. 获取 ETF 列表（Tushare fund_basic）
  2. 获取日线数据用于网格适配性因子打分
  3. 按 grid_suitability 因子排序，筛选 top-N
  4. 对候选 ETF 从 QMT API 获取 5 分钟 K 线
  5. 逐个回测网格策略
  6. 生成排名报告

用法:
  python3.14 etf_grid_scanner.py                        # 默认: 今年至今, top-10
  python3.14 etf_grid_scanner.py --top 5 --capital 50000  # 自定义参数
  python3.14 etf_grid_scanner.py --start 20260101 --end 20260720 --top 15
  python3.14 etf_grid_scanner.py --stocks 159692.SZ,510050.SH  # 指定 ETF
  python3.14 etf_grid_scanner.py --skip-factor --stocks 159915.SZ,510300.SH  # 跳过筛选直接跑
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent))

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

import numpy as np
import pandas as pd

from src.config import DATA_DIR, QMT_API_BASE_URL
from src.grid.grid_backtest import GridBacktestEngine
from src.grid.grid_params import GridParams
from src.grid.grid_report import generate_report
from src.factors.grid_suitability import GridSuitabilityFactor


# ─── 配置 ────────────────────────────────────────────────────
DEFAULT_TOP_N = 10
DEFAULT_CAPITAL = 100_000
DEFAULT_START = "20260101"
ETF_MIN_DAYS = 60          # 最少交易日数
ETF_MIN_AMOUNT = 5000       # 最小日均成交额 (千元)
ETF_MAX_PRICE = 20           # 最高单价 (元)，太贵不适合小资金网格
ETF_MIN_PRICE = 0.3          # 最低单价 (元）

# ETF 交易费用 (比股票低，无印花税)
ETF_BUY_COMMISSION = 0.00015  # 万1.5
ETF_SELL_COMMISSION = 0.00015
ETF_STAMP_TAX = 0.0          # ETF 免印花税

# QMT API 访问
TIMEOUT = 120
RETRIES = 2


# ─── 数据获取 ────────────────────────────────────────────────

def get_etf_list() -> pd.DataFrame:
    """从 Tushare 获取全市场 ETF 列表（股票型 + 跨境型）"""
    import tushare as ts
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
    df = pro.fund_basic(market="E")
    if df is None or df.empty:
        raise RuntimeError("Tushare fund_basic 返回空")

    # 筛选已上市的 ETF
    df = df[df["status"] == "L"].copy()
    # 股票型 / QDII / 混合型
    target_types = ["股票型", "QDII", "混合型"]
    df = df[df["fund_type"].isin(target_types)]
    print(f"✅ ETF 列表: {len(df)} 只 (股票型/QDII/混合型)")
    return df[["ts_code", "name", "fund_type", "found_date"]].reset_index(drop=True)


def fetch_etf_daily_data(etf_df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """获取 ETF 日线数据。

    优先使用本地 DB (stock_data.db) 中的 QMT 导出数据，
    回退到 Tushare (fund_daily + 复权因子)。
    """
    ts_codes = etf_df["ts_code"].tolist()

    # ── 方案1: 本地 SQLite (QMT 导出) ──
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "data", "stock_data.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path, timeout=5)
        placeholders = ",".join(["?"] * len(ts_codes))
        df_db = pd.read_sql_query(
            f"SELECT trade_date, ts_code, open, high, low, close, vol, amount "
            f"FROM daily_price WHERE ts_code IN ({placeholders}) "
            f"AND trade_date >= ? AND trade_date <= ? "
            f"ORDER BY trade_date",
            conn, params=ts_codes + [start, end]
        )
        conn.close()
        if not df_db.empty:
            codes_found = df_db["ts_code"].nunique()
            print(f"✅ 本地 DB 获取 {len(df_db)} 行日线，覆盖 {codes_found}/{len(ts_codes)} 只 ETF")
            return df_db
    print("⚠️ 本地 DB 无 ETF 日线数据，回退到 Tushare...")

    # ── 方案2: Tushare (较慢，约 3-5 分钟) ──
    import tushare as ts
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
    frames = []
    for i, row in etf_df.iterrows():
        code = row["ts_code"]
        try:
            df_fund = pro.fund_daily(ts_code=code, start_date=start, end_date=end)
            if df_fund is not None and not df_fund.empty:
                df_fund = df_fund.rename(columns={
                    "trade_date": "trade_date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "vol": "vol",
                    "amount": "amount",
                })
                cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
                df_fund["ts_code"] = code
                frames.append(df_fund[cols])
            if (i + 1) % 50 == 0:
                print(f"  Tushare 进度: {i+1}/{len(etf_df)}")
            time.sleep(0.06)  # 避免限频
        except Exception:
            continue
    if not frames:
        raise RuntimeError("Tushare 也未获取到 ETF 日线数据")
    result = pd.concat(frames, ignore_index=True)
    print(f"✅ Tushare 获取 {len(result)} 行日线，{result['ts_code'].nunique()} 只 ETF")
    return result


def fetch_minute_kline_qmt(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """从 QMT API 获取 5 分钟 K 线"""
    import requests
    url = QMT_API_BASE_URL.rstrip("/") + "/api/minute_kline"
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params={
                "ts_code": ts_code, "start": start, "end": end
            }, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                return pd.DataFrame()
            df = pd.DataFrame(items)
            for col in ["open", "high", "low", "close", "vol", "amount", "is_trading"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_values("bar_time").reset_index(drop=True)
        except Exception:
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
    return pd.DataFrame()


def fetch_minute_kline_eastmoney(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    """从东方财富 API 获取 5 分钟 K 线（免费、无限频）"""
    code = ts_code.split(".")[0]
    # 判断市场
    if ts_code.endswith(".SH"):
        market = "1"
    elif ts_code.endswith(".SZ"):
        market = "0"
    else:
        return None

    secid = f"{market}.{code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&"
        f"fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=5&fqt=1&beg={start}&end={end}&lmt=10000"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("data") or not data["data"].get("klines"):
            return None
        rows = []
        for k in data["data"]["klines"]:
            parts = k.split(",")
            rows.append({
                "bar_time": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "vol": int(float(parts[5])),
                "amount": float(parts[6]),
            })
        return pd.DataFrame(rows).sort_values("bar_time").reset_index(drop=True)
    except Exception:
        return None


# ─── ETF 预筛选 ──────────────────────────────────────────────

def prefilter_etfs(df: pd.DataFrame) -> pd.DataFrame:
    """基础筛选：流动性、价格区间、数据充足度"""
    # 按 ts_code 统计
    stats = df.groupby("ts_code").agg(
        latest_close=("close", "last"),
        avg_amount=("amount", "mean"),
        days=("close", "count"),
    ).reset_index()

    stats = stats[
        (stats["days"] >= ETF_MIN_DAYS) &
        (stats["avg_amount"] >= ETF_MIN_AMOUNT) &
        (stats["latest_close"] >= ETF_MIN_PRICE) &
        (stats["latest_close"] <= ETF_MAX_PRICE)
    ]
    print(f"📊 基础筛选后: {len(stats)} 只 ETF (≥{ETF_MIN_DAYS}天, 日均成交额≥{ETF_MIN_AMOUNT}千元, 价格{ETF_MIN_PRICE}~{ETF_MAX_PRICE}元)")

    # 只保留通过筛选的 ETF 数据
    valid_codes = set(stats["ts_code"])
    return df[df["ts_code"].isin(valid_codes)].copy()


# ─── 因子打分 ────────────────────────────────────────────────

def score_etfs(df: pd.DataFrame) -> pd.DataFrame:
    """计算 grid_suitability 因子并排名"""
    factor = GridSuitabilityFactor()
    df = factor.calculate(df)

    # 取最新交易日得分排名
    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date].dropna(subset=["grid_suitability"])
    ranked = latest.sort_values("grid_suitability", ascending=False).reset_index(drop=True)
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked


# ─── 网格回测 ────────────────────────────────────────────────

def build_etf_grid_params(ts_code: str, minute_df: pd.DataFrame, n_levels: int = 10) -> GridParams:
    """为 ETF 构建网格参数（基于 5 分钟数据的价格范围）"""
    if minute_df.empty:
        raise ValueError(f"{ts_code}: 无分钟数据")
    close = minute_df["close"]
    price_low = float(close.min())
    price_high = float(close.max())

    # 添加 ±5% 的边际
    margin = (price_high - price_low) * 0.05
    price_lower = max(round(price_low - margin, 3), 0.01)
    price_upper = round(price_high + margin, 3)
    price_upper = max(price_upper, price_lower + 0.01)

    return GridParams(
        price_upper=price_upper,
        price_lower=price_lower,
        grid_levels=n_levels,
        grid_mode="ratio",         # 等比间距更适合 ETF
        order_shares=100,          # 100 份/格（ETF 1手=100份）
        base_shares=0,             # 初始无底仓
        buy_commission=ETF_BUY_COMMISSION,
        sell_commission=ETF_SELL_COMMISSION,
        stamp_tax=ETF_STAMP_TAX,
    )


def backtest_one_etf(ts_code: str, minute_df: pd.DataFrame, params: GridParams,
                     capital: float) -> dict:
    """对单只 ETF 执行网格回测"""
    engine = GridBacktestEngine(initial_capital=capital)
    result = engine.run(minute_df, params)

    # 计算关键指标
    trades = result.trades
    buy_trades = [t for t in trades if t.action == "BUY"]
    sell_trades = [t for t in trades if t.action == "SELL"]
    total_trades = len(trades)

    if total_trades == 0:
        return {
            "ts_code": ts_code,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "total_trades": 0,
            "buy_trades": 0,
            "sell_trades": 0,
            "grid_pnl": 0.0,
            "base_pnl": 0.0,
            "commission": 0.0,
            "final_nav": capital,
            "nav_series": None,
            "trades": [],
            "params": params,
        }

    nav = pd.DataFrame(result.nav)
    # 总收益率
    total_return = (nav["total_value"].iloc[-1] / capital - 1) * 100 if len(nav) > 0 else 0

    # 年化收益率
    if len(nav) > 1:
        days = len(nav)
        annual_return = ((1 + total_return / 100) ** (252 / days) - 1) * 100
    else:
        annual_return = 0

    # 最大回撤
    if len(nav) > 1:
        cummax = nav["total_value"].cummax()
        drawdown = (nav["total_value"] - cummax) / cummax * 100
        max_dd = drawdown.min()
    else:
        max_dd = 0

    # 夏普
    if len(nav) > 5:
        nav["daily_return"] = nav["total_value"].pct_change()
        sharpe = nav["daily_return"].mean() / nav["daily_return"].std() * np.sqrt(252) if nav["daily_return"].std() > 0 else 0
    else:
        sharpe = 0

    # 损益拆分
    grid_pnl = sum(t.net for t in trades)
    base_pnl = result.base_pnl if hasattr(result, "base_pnl") else (nav["total_value"].iloc[-1] - capital - grid_pnl)
    commission = sum(t.commission for t in trades)

    return {
        "ts_code": ts_code,
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "total_trades": total_trades,
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "grid_pnl": round(grid_pnl, 2),
        "base_pnl": round(base_pnl, 2),
        "commission": round(commission, 2),
        "final_nav": round(nav["total_value"].iloc[-1], 2) if len(nav) > 0 else capital,
        "nav_series": nav,
        "trades": trades,
        "params": params,
    }


# ─── 主流程 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETF 网格交易扫描与回测")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="回测 top-N ETF")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金")
    parser.add_argument("--start", type=str, default=DEFAULT_START, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None, help="结束日期 (默认今天)")
    parser.add_argument("--stocks", type=str, default=None, help="指定 ETF 代码 (逗号分隔, 跳过因子筛选)")
    parser.add_argument("--skip-factor", action="store_true", help="跳过因子预筛选 (需 --stocks)")
    parser.add_argument("--levels", type=int, default=10, help="网格层数")
    parser.add_argument("--test", action="store_true", help="测试模式: 只跑 top-3, 显示进度")
    parser.add_argument("--use-eastmoney", action="store_true", help="强制使用东方财富 API 补充分钟数据")
    args = parser.parse_args()

    end_date = args.end or dt.date.today().strftime("%Y%m%d")
    start_date = args.start

    print("=" * 70)
    print("  📊 ETF 网格交易扫描器")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print(f"  初始资金: ¥{args.capital:,.0f}")
    print(f"  网格层数: {args.levels}")
    print("=" * 70)

    # ── Step 1: ETF 列表 ──
    print("\n[1/5] 获取 ETF 列表...")
    etf_list = get_etf_list()

    # ── Step 2: 日线数据 + 因子筛选 ──
    if args.stocks:
        stock_codes = [s.strip() for s in args.stocks.split(",")]
        print(f"\n[2/5] 🎯 使用指定 ETF: {stock_codes}")
        ranked = pd.DataFrame({"ts_code": stock_codes, "rank": range(1, len(stock_codes) + 1)})
    else:
        print(f"\n[2/5] 获取日线数据 ({start_date}~{end_date})...")
        daily_df = fetch_etf_daily_data(etf_list, start_date, end_date)
        daily_df = prefilter_etfs(daily_df)

        print(f"\n[3/5] 计算 grid_suitability 因子...")
        daily_df = daily_df.sort_values(["ts_code", "trade_date"])
        ranked = score_etfs(daily_df)

    # 合并 ETF 名称
    ranked = ranked.merge(etf_list[["ts_code", "name"]], on="ts_code", how="left")
    top_n = min(args.top, len(ranked))
    if top_n == 0:
        print("❌ 无符合条件的 ETF")
        return

    print(f"\n{'─' * 70}")
    print(f"  🏆 grid_suitability 因子排名 Top-{top_n}:")
    print(f"{'─' * 70}")
    if "grid_suitability" in ranked.columns:
        for _, row in ranked.head(top_n).iterrows():
            print(f"  #{row['rank']:<3} {row['ts_code']:<12} {row.get('name','?'):<24} "
                  f"分数={row.get('grid_suitability',0):+.3f} "
                  f"现价=¥{row.get('latest_close', row.get('close', 0)):.3f}")
    else:
        for _, row in ranked.head(top_n).iterrows():
            print(f"  #{row['rank']:<3} {row['ts_code']:<12} {row.get('name','?')}")

    # ── Step 4: 5分钟数据 + 回测 ──
    print(f"\n[4/5] 获取 5分钟 K 线数据 (QMT API + 东方财富备用)...")

    backtest_results = []
    top_codes = ranked.head(top_n)["ts_code"].tolist()

    for i, ts_code in enumerate(top_codes):
        name = ranked[ranked["ts_code"] == ts_code]["name"].values[0] if len(ranked[ranked["ts_code"] == ts_code]) > 0 else "?"
        print(f"\n  ({i+1}/{top_n}) {ts_code} {name}")

        # 获取分钟数据
        minute_df = fetch_minute_kline_qmt(ts_code, start_date, end_date)

        if minute_df.empty and args.use_eastmoney:
            print(f"    QMT 无数据，尝试东方财富...")
            em_df = fetch_minute_kline_eastmoney(ts_code, start_date, end_date)
            if em_df is not None and not em_df.empty:
                minute_df = em_df
                print(f"    ✅ 东方财富: {len(minute_df)} bars")
            else:
                print(f"    ❌ 无分钟数据，跳过")
                backtest_results.append({"ts_code": ts_code, "error": "无分钟数据"})
                continue
        elif minute_df.empty:
            print(f"    ⚠️ QMT API 无数据 (API 可能离线)，尝试东方财富...")
            em_df = fetch_minute_kline_eastmoney(ts_code, start_date, end_date)
            if em_df is not None and not em_df.empty:
                minute_df = em_df
                print(f"    ✅ 东方财富: {len(minute_df)} bars")
            else:
                print(f"    ❌ 无分钟数据，跳过")
                backtest_results.append({"ts_code": ts_code, "error": "无分钟数据"})
                continue
        else:
            print(f"    ✅ QMT API: {len(minute_df)} bars")

        # 构建网格参数
        try:
            params = build_etf_grid_params(ts_code, minute_df, n_levels=args.levels)
        except ValueError as e:
            print(f"    ❌ 参数构建失败: {e}")
            backtest_results.append({"ts_code": ts_code, "error": str(e)})
            continue

        # 回测
        print(f"    📐 网格: {params.price_lower:.3f}~{params.price_upper:.3f}, "
              f"{params.grid_levels}层, 等比间距")
        result = backtest_one_etf(ts_code, minute_df, params, args.capital)
        result["name"] = name
        backtest_results.append(result)

        print(f"    📈 总收益={result.get('total_return',0):+.2f}%  "
              f"年化={result.get('annual_return',0):+.2f}%  "
              f"回撤={result.get('max_drawdown',0):.2f}%  "
              f"夏普={result.get('sharpe',0):.3f}  "
              f"成交={result.get('total_trades',0)}笔")

    # ── Step 5: 综合排名 ──
    print(f"\n[5/5] 生成综合排名报告...")
    valid_results = [r for r in backtest_results if "error" not in r and r.get("total_trades", 0) > 0]
    if not valid_results:
        print("❌ 所有 ETF 回测失败")
        return

    # 综合评分: 年化收益(40%) + 夏普(30%) + 最大回撤(30%, 取反)
    for r in valid_results:
        r["composite_score"] = round(
            0.4 * r["annual_return"] +
            0.3 * r["sharpe"] * 100 -
            0.3 * abs(r["max_drawdown"]),
            2
        )

    ranked_results = sorted(valid_results, key=lambda x: x["composite_score"], reverse=True)

    print(f"\n{'=' * 90}")
    print(f"  🏆 ETF 网格交易综合排名")
    print(f"  回测区间: {start_date} ~ {end_date} | 初始资金: ¥{args.capital:,.0f} | 网格层数: {args.levels}")
    print(f"{'=' * 90}")
    print(f"{'排名':<5} {'代码':<12} {'名称':<20} {'总收益':>8} {'年化':>8} {'回撤':>8} {'夏普':>7} {'成交':>5} {'综合':>8}")
    print(f"{'─' * 90}")

    for i, r in enumerate(ranked_results):
        name = r.get("name", "?")[:18]
        print(f"{i+1:<5} {r['ts_code']:<12} {name:<20} "
              f"{r['total_return']:>+7.2f}% {r['annual_return']:>+7.2f}% "
              f"{r['max_drawdown']:>7.2f}% {r['sharpe']:>7.3f} "
              f"{r['total_trades']:>5} {r['composite_score']:>+8.2f}")

    print(f"{'─' * 90}")

    # ── 逐只详情 ──
    for r in ranked_results:
        print(f"\n{'─' * 70}")
        print(f"  📋 {r['ts_code']} {r.get('name','?')} 详情")
        print(f"{'─' * 70}")
        print(f"  网格区间: ¥{r['params'].price_lower:.4f} ~ ¥{r['params'].price_upper:.4f}")
        print(f"  网格层数: {r['params'].grid_levels} ({r['params'].grid_mode})")
        print(f"  总收益: {r['total_return']:+.2f}% | 年化: {r['annual_return']:+.2f}%")
        print(f"  最大回撤: {r['max_drawdown']:.2f}% | 夏普: {r['sharpe']:.3f}")
        print(f"  总成交: {r['total_trades']}笔 (买{r['buy_trades']}/卖{r['sell_trades']})")
        print(f"  网格损益: ¥{r['grid_pnl']:+,.2f} | 手续费: ¥{r['commission']:,.2f}")
        print(f"  最终净值: ¥{r['final_nav']:,.2f}")

    # ── 保存报告 ──
    out_dir = DATA_DIR / "results" / (dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_etf_grid")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 生成汇总报告
    report_lines = [
        "# ETF 网格交易扫描报告",
        f"**生成时间**: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**回测区间**: {start_date} ~ {end_date}",
        f"**初始资金**: ¥{args.capital:,.0f}",
        "",
        "## 综合排名",
        "",
        "| 排名 | 代码 | 名称 | 总收益 | 年化 | 最大回撤 | 夏普 | 成交 | 综合分 |",
        "|------|------|------|--------|------|----------|------|------|--------|",
    ]
    for i, r in enumerate(ranked_results):
        report_lines.append(
            f"| {i+1} | {r['ts_code']} | {r.get('name','?')} | "
            f"{r['total_return']:+.2f}% | {r['annual_return']:+.2f}% | "
            f"{r['max_drawdown']:.2f}% | {r['sharpe']:.3f} | "
            f"{r['total_trades']} | {r['composite_score']:+.2f} |"
        )

    report_lines += ["", "## 各 ETF 详情", ""]
    for r in ranked_results:
        report_lines += [
            f"### {r['ts_code']} {r.get('name','?')}",
            "",
            f"- **总收益**: {r['total_return']:+.2f}% / 年化 {r['annual_return']:+.2f}%",
            f"- **最大回撤**: {r['max_drawdown']:.2f}% / 夏普 {r['sharpe']:.3f}",
            f"- **成交**: {r['total_trades']}笔 (买{r['buy_trades']}/卖{r['sell_trades']})",
            f"- **网格损益**: ¥{r['grid_pnl']:+,.2f} / 手续费 ¥{r['commission']:,.2f}",
            f"- **最终净值**: ¥{r['final_nav']:,.2f}",
            f"- **网格参数**: {r['params'].price_lower:.4f} ~ {r['params'].price_upper:.4f}, "
            f"{r['params'].grid_levels}层 {r['params'].grid_mode}",
            "",
        ]

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\n✅ 报告已保存: {report_path}")
    print(f"   输出目录: {out_dir}")


if __name__ == "__main__":
    main()
