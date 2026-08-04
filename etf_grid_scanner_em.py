#!/usr/bin/env python3
"""
ETF 网格交易扫描与回测 — 新浪财经数据源

数据源（稳定免费，无需 API Key）：
- ETF 列表：东方财富 push2 API
- 日线数据：新浪财经 API（~10个月，用于 grid_suitability 因子筛选）
- 5分钟数据：新浪财经 API（~2个月，用于网格回测）

QMT 5分钟数据就绪后，使用 --force-qmt 切换回 QMT API。

用法:
  python3.14 etf_grid_scanner_em.py --test         # top-3 快速测试
  python3.14 etf_grid_scanner_em.py --top 10        # top-10 完整扫描
  python3.14 etf_grid_scanner_em.py --stocks 510300.SH,159915.SZ  # 指定ETF
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["NO_PROXY"] = "*"
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import requests
requests.packages.urllib3.disable_warnings()

from src.grid.grid_backtest import GridBacktestEngine
from src.grid.grid_params import GridParams

# ─── 配置 ────────────────────────────────────────────────
DEFAULT_TOP_N = 10
DEFAULT_CAPITAL = 100_000
DEFAULT_START = "20260101"
DEFAULT_END = dt.date.today().strftime("%Y%m%d")
ETF_MIN_DAYS = 60
ETF_MIN_AMOUNT_DAILY = 1_000_000
ETF_MAX_PRICE = 20
ETF_MIN_PRICE = 0.3
API_TIMEOUT = 30
API_RETRIES = 2
DELAY = 0.3

# ETF 网格交易参数
BUY_COMM = 0.00015   # 万1.5
SELL_COMM = 0.00015
STAMP = 0.0          # ETF 免印花税


# ══════════════════════════════════════════════════════════
#  数据获取
# ══════════════════════════════════════════════════════════

def _req(url: str, retries: int = API_RETRIES) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, verify=False, timeout=API_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
    return None


def get_etf_list() -> pd.DataFrame:
    """ETF 列表（Tushare）"""
    import tushare as ts
    pro = ts.pro_api()
    df = pro.fund_basic(market='E', status='L')
    df = df[df['fund_type'].isin(['股票型', 'QDII', '混合型'])].copy()
    # 只保留 2026 年前上市且有规模的
    df['list_date'] = pd.to_numeric(df.get('list_date', '20260101'), errors='coerce')
    df = df[df['list_date'] < 20260101].copy()
    result = pd.DataFrame({
        "ts_code": df["ts_code"],
        "name": df["name"],
    })
    print(f"✅ Tushare ETF 列表: {len(result)} 只 (2026年前上市, 股票/QDII/混合)")
    return result


def fetch_daily_tushare(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    """Tushare ETF 日线数据"""
    import tushare as ts
    pro = ts.pro_api()
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.rename(columns={"trade_date": "trade_date"})
    # fund_daily already has: ts_code, trade_date, open, high, low, close, vol, amount
    return df[df["close"] > 0].copy()


def fetch_minute_sina(ts_code: str, n_bars: int = 2000) -> pd.DataFrame | None:
    """新浪 5 分钟 K 线"""
    code = ts_code.split(".")[0]
    prefix = "sh" if ts_code.endswith(".SH") else "sz"
    symbol = f"{prefix}{code}"
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen={n_bars}"
    )
    r = _req(url)
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not data or not isinstance(data, list) or len(data) < 100:
        return None
    rows = []
    for k in data:
        rows.append({
            "bar_time": k["day"],
            "open": float(k["open"]),
            "close": float(k["close"]),
            "high": float(k["high"]),
            "low": float(k["low"]),
            "vol": int(float(k["volume"])),
            "amount": 0.0,
        })
    df = pd.DataFrame(rows)
    df["amount"] = df["vol"] * df["close"]
    return df.sort_values("bar_time").reset_index(drop=True)


def fetch_minute_qmt(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    """QMT API 5 分钟 K 线"""
    from src.config import QMT_API_BASE_URL
    url = QMT_API_BASE_URL.rstrip("/") + "/api/minute_kline"
    for attempt in range(3):
        try:
            r = requests.get(url, params={
                "ts_code": ts_code, "start": start, "end": end,
            }, timeout=120)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return None
            df = pd.DataFrame(items)
            for col in ["open", "high", "low", "close", "vol", "amount"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_values("bar_time").reset_index(drop=True)
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
    return None


# ══════════════════════════════════════════════════════════
#  网格适配性因子
# ══════════════════════════════════════════════════════════

def calculate_grid_suitability(df: pd.DataFrame) -> pd.DataFrame:
    """
    4 维度评分:
    1. 震荡纯度 = 日内振幅 / 中期漂移 (2x)
    2. 流动性 = 日均成交额
    3. 均值回归 = 布林带内时间占比
    4. 趋势惩罚 = |120日收益| 越大扣分越多
    """
    df = df.copy().sort_values(["ts_code", "trade_date"])
    g = df.groupby("ts_code")

    # 1. 震荡纯度
    df["intraday_range"] = (df["high"] - df["low"]) / df["close"]
    df["avg_range_60"] = g["intraday_range"].transform(
        lambda x: x.rolling(60, min_periods=20).mean())
    df["return_120d"] = g["close"].transform(
        lambda x: x.pct_change(periods=119).fillna(0))
    df["total_drift_120"] = np.abs(df["return_120d"])
    df["purity_raw"] = np.where(df["total_drift_120"] > 0.001,
                                df["avg_range_60"] / df["total_drift_120"], 0.0)
    df["purity_score"] = np.minimum(df["purity_raw"] / 0.4, 2.0)

    # 2. 流动性
    df["avg_amount_20d"] = g["amount"].transform(
        lambda x: x.rolling(20, min_periods=10).mean())
    df["liquidity_score"] = np.minimum(df["avg_amount_20d"] / 5_000_000, 1.0)

    # 3. 均值回归
    ma = g["close"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    std = g["close"].transform(lambda x: x.rolling(20, min_periods=10).std())
    in_bands = (df["close"] >= ma - 2 * std) & (df["close"] <= ma + 2 * std)
    df["bands_ratio"] = g["close"].transform(  # hack: apply on any col
        lambda x: in_bands[x.index].rolling(120, min_periods=40).mean())
    df["reversion_score"] = np.minimum(df["bands_ratio"] / 0.6, 1.0)

    # 4. 趋势惩罚
    df["trend_score"] = np.maximum(0, 1.0 - (df["total_drift_120"] / 0.50) ** 2)

    # 综合: 纯度(2x) + 流动性 + 回归, × 趋势惩罚
    df["grid_suitability_raw"] = (
        (2.0 * df["purity_score"] + df["liquidity_score"] + df["reversion_score"])
        / 4.0 * df["trend_score"]
    )
    df["grid_suitability"] = df.groupby("trade_date")[
        "grid_suitability_raw"].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 1e-10 else 0.0)
    return df


def prefilter_etfs(df: pd.DataFrame) -> pd.DataFrame:
    """流动性 + 价格 + 最少天数"""
    stats = df.groupby("ts_code").agg(
        latest_close=("close", "last"),
        avg_amount=("amount", "mean"),
        days=("close", "count"),
    ).reset_index()
    stats = stats[
        (stats["days"] >= ETF_MIN_DAYS) &
        (stats["avg_amount"] >= ETF_MIN_AMOUNT_DAILY) &
        (stats["latest_close"] >= ETF_MIN_PRICE) &
        (stats["latest_close"] <= ETF_MAX_PRICE)
    ]
    print(f"📊 基础筛选: {len(stats)} 只 (≥{ETF_MIN_DAYS}天, "
          f"日均≥{ETF_MIN_AMOUNT_DAILY/1e4:.0f}万, "
          f"¥{ETF_MIN_PRICE}~{ETF_MAX_PRICE})")
    valid = set(stats["ts_code"])
    return df[df["ts_code"].isin(valid)].copy()


# ══════════════════════════════════════════════════════════
#  回测包装
# ══════════════════════════════════════════════════════════

def build_grid_params(minute_df: pd.DataFrame, n_levels: int = 10) -> GridParams:
    close = minute_df["close"]
    p_low = float(close.min())
    p_high = float(close.max())
    margin = (p_high - p_low) * 0.05
    price_lower = max(round(p_low - margin, 4), 0.001)
    price_upper = max(round(p_high + margin, 4), price_lower + 0.01)
    return GridParams(
        price_upper=price_upper,
        price_lower=price_lower,
        grid_levels=n_levels,
        grid_mode="ratio",
        order_shares=100,
        base_shares=0,
        buy_commission=BUY_COMM,
        sell_commission=SELL_COMM,
        stamp_tax=STAMP,
    )


def backtest_one(ts_code: str, minute_df: pd.DataFrame,
                 params: GridParams, capital: float) -> dict:
    engine = GridBacktestEngine(initial_capital=capital)
    result = engine.run(minute_df, params)
    trades = result.trades  # pd.DataFrame
    if trades is None or len(trades) == 0:
        return {"ts_code": ts_code, "error": "无成交"}
    n_buys = int((trades["action"] == "BUY").sum()) if "action" in trades.columns else 0
    n_sells = int((trades["action"] == "SELL").sum()) if "action" in trades.columns else 0
    nav = result.nav_series
    if "total_value" not in nav.columns:
        nav["total_value"] = nav["nav"]
    if len(nav) < 2:
        return {"ts_code": ts_code, "error": "数据不足"}
    total_ret = (nav["total_value"].iloc[-1] / capital - 1) * 100
    days = len(nav)
    annual_ret = ((1 + total_ret / 100) ** (252 / max(days, 1)) - 1) * 100
    cummax = nav["total_value"].cummax()
    max_dd = ((nav["total_value"] - cummax) / cummax * 100).min()
    if len(nav) > 5:
        dr = nav["total_value"].pct_change()
        sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else 0.0
    else:
        sharpe = 0.0
    att = getattr(result, "attribution", {})
    grid_pnl = att.get("total_return", 0) * capital
    commission = att.get("total_commission", 0)
    final_val = nav["total_value"].iloc[-1]

    return {
        "ts_code": ts_code,
        "total_return": round(total_ret, 2),
        "annual_return": round(annual_ret, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "total_trades": len(trades),
        "buy_trades": n_buys,
        "sell_trades": n_sells,
        "grid_pnl": round(grid_pnl, 2),
        "commission": round(commission, 2),
        "final_nav": round(final_val, 2),
        "data_bars": len(minute_df),
        "data_start": str(minute_df["bar_time"].iloc[0])[:10],
        "data_end": str(minute_df["bar_time"].iloc[-1])[:10],
        "params": params,
    }


# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ETF 网格交易扫描与回测")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--stocks", type=str, default=None)
    parser.add_argument("--levels", type=int, default=10)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--force-qmt", action="store_true")
    args = parser.parse_args()

    if args.test:
        args.top = 3

    src_label = "QMT" if args.force_qmt else "新浪财经"
    print("=" * 70)
    print("  📊 ETF 网格交易扫描器")
    print(f"  数据源: {src_label} | {args.levels}层等比 | "
          f"资金: ¥{args.capital:,.0f} | 佣金: 万1.5")
    print("=" * 70)

    # ── 1. ETF 列表 ──
    if args.stocks:
        codes = [s.strip() for s in args.stocks.split(",")]
        df_list = pd.DataFrame({"ts_code": codes, "name": codes})
        print(f"\n🎯 指定 ETF: {codes}")
    else:
        print("\n[1/4] 获取 ETF 列表...")
        df_list = get_etf_list()
        print(f"  候选 ETF: {len(df_list)} 只")

    if len(df_list) == 0:
        print("❌ 无 ETF")
        return

    # ── 2. 日线 + 因子 ──
    if args.stocks:
        ranked = pd.DataFrame({
            "ts_code": codes,
            "grid_suitability": [0.0] * len(codes),
            "rank": range(1, len(codes) + 1),
        })
        ranked = ranked.merge(df_list[["ts_code", "name"]], on="ts_code", how="left")
    else:
        print("\n[2/4] 日线数据 + 网格适配性因子...")
        all_codes = df_list["ts_code"].tolist()
        daily_frames = []
        total = len(all_codes)
        for i, code in enumerate(all_codes):
            df = fetch_daily_tushare(code, args.start, args.end)
            if df is not None and not df.empty:
                # 过滤到 2026 年
                df = df[df["trade_date"] >= args.start].copy()
                if not df.empty:
                    daily_frames.append(df)
            if (i + 1) % 50 == 0:
                print(f"  日线: {i+1}/{total} ({len(daily_frames)} 只有效)")
            time.sleep(DELAY)

        if not daily_frames:
            print("❌ 无日线数据")
            return
        daily_all = pd.concat(daily_frames, ignore_index=True)
        print(f"✅ 日线: {len(daily_all)} 行 / {daily_all['ts_code'].nunique()} 只")

        daily_all = prefilter_etfs(daily_all)
        if daily_all.empty:
            print("❌ 全部未通过基础筛选")
            return

        daily_all = daily_all.sort_values(["ts_code", "trade_date"])
        daily_all = calculate_grid_suitability(daily_all)

        latest_date = daily_all["trade_date"].max()
        latest = daily_all[daily_all["trade_date"] == latest_date]
        latest = latest.dropna(subset=["grid_suitability"])
        ranked = latest.sort_values("grid_suitability", ascending=False).reset_index(drop=True)
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked = ranked.merge(df_list[["ts_code", "name"]], on="ts_code", how="left")

    top_n = min(args.top, len(ranked))
    print(f"\n{'─' * 65}")
    print(f"  🏆 网格适配性 Top-{top_n}:")
    print(f"{'─' * 65}")
    for _, row in ranked.head(top_n).iterrows():
        print(f"  #{int(row['rank']):<3} {row['ts_code']:<12} "
              f"{str(row.get('name','?'))[:22]:<24} "
              f"GS={row['grid_suitability']:+.3f}")

    # ── 3. 5分钟 + 回测 ──
    print(f"\n[3/4] 5分钟K线 + 回测...")
    top_codes = ranked.head(top_n)["ts_code"].tolist()
    results = []

    if args.force_qmt:
        print("  ⚠️ 使用 QMT API（需要 Windows 端 API 服务运行）")

    for i, ts_code in enumerate(top_codes):
        name_row = ranked[ranked["ts_code"] == ts_code]
        name = name_row["name"].values[0] if len(name_row) > 0 else "?"

        if args.force_qmt:
            minute = fetch_minute_qmt(ts_code, args.start, args.end)
        else:
            minute = fetch_minute_sina(ts_code)

        if minute is None or minute.empty:
            print(f"\n  ({i+1}/{top_n}) {ts_code} {name} ❌ 无5分钟数据")
            results.append({"ts_code": ts_code, "name": name, "error": "无数据"})
            continue

        print(f"\n  ({i+1}/{top_n}) {ts_code} {name} "
              f"✅ {len(minute)} bars "
              f"({minute['bar_time'].iloc[0][:10]} ~ "
              f"{minute['bar_time'].iloc[-1][:10]})")

        try:
            params = build_grid_params(minute, n_levels=args.levels)
        except (ValueError, IndexError) as e:
            print(f"    ❌ 参数错误: {e}")
            results.append({"ts_code": ts_code, "name": name, "error": str(e)})
            continue

        print(f"    📐 ¥{params.price_lower:.4f} ~ ¥{params.price_upper:.4f}")
        r = backtest_one(ts_code, minute, params, args.capital)
        r["name"] = name
        results.append(r)

        print(f"    📈 收益={r.get('total_return',0):+.2f}%  "
              f"年化={r.get('annual_return',0):+.2f}%  "
              f"回撤={r.get('max_drawdown',0):.2f}%  "
              f"夏普={r.get('sharpe',0):.3f}  "
              f"成交={r.get('total_trades',0)}笔")
        time.sleep(0.5)

    # ── 4. 排名 ──
    print(f"\n[4/4] 综合排名...")
    valid = [r for r in results if "error" not in r]
    if not valid:
        print("❌ 全部回测失败")
        return

    for r in valid:
        r["composite"] = round(
            0.4 * r.get("annual_return", 0) +
            0.3 * r.get("sharpe", 0) * 100 -
            0.3 * abs(r.get("max_drawdown", 0)), 2
        )
    ranked_r = sorted(valid, key=lambda x: x["composite"], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"  🏆 ETF 网格交易回测排名 ({src_label} 数据)")
    print(f"  区间: {args.start}~{args.end} | 资金: ¥{args.capital:,.0f} | "
          f"{args.levels}层等比")
    print(f"{'=' * 100}")
    print(f"{'#':<4} {'代码':<12} {'名称':<20} {'收益':>8} {'年化':>8} "
          f"{'回撤':>8} {'夏普':>7} {'成交':>5} {'综合':>8}")
    print(f"{'─' * 100}")
    for i, r in enumerate(ranked_r):
        name = str(r.get("name", "?"))[:18]
        print(f"{i+1:<4} {r['ts_code']:<12} {name:<20} "
              f"{r['total_return']:>+7.2f}% {r['annual_return']:>+7.2f}% "
              f"{r['max_drawdown']:>7.2f}% {r['sharpe']:>7.3f} "
              f"{r['total_trades']:>5} {r['composite']:>+8.2f}")
    print(f"{'─' * 100}")

    # 详情
    for r in ranked_r:
        print(f"\n{'─' * 55}")
        print(f"  📋 {r['ts_code']} {r.get('name','?')}")
        print(f"{'─' * 55}")
        print(f"  网格: ¥{r['params'].price_lower:.4f} ~ "
              f"¥{r['params'].price_upper:.4f}")
        print(f"  数据: {r['data_bars']} bars "
              f"({r.get('data_start','')} ~ {r.get('data_end','')})")
        print(f"  收益={r['total_return']:+.2f}% 年化={r['annual_return']:+.2f}%")
        print(f"  回撤={r['max_drawdown']:.2f}% 夏普={r['sharpe']:.3f}")
        print(f"  成交={r['total_trades']}笔 (买{r['buy_trades']}/卖{r['sell_trades']})")

    # 保存报告
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).parent.parent / "data" / "results" / f"{stamp}_etf_grid_sina"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# ETF 网格交易扫描报告",
        f"**时间**: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"**数据源**: {src_label}",
        f"**区间**: {args.start}~{args.end} | **资金**: ¥{args.capital:,.0f}",
        f"**网格**: {args.levels}层等比 | **佣金**: 万1.5(免印花税)",
        "",
        "## 综合排名",
        "| # | 代码 | 名称 | 总收益 | 年化 | 回撤 | 夏普 | 成交 | 综合 |",
        "|---|------|------|--------|------|------|------|------|------|",
    ]
    for i, r in enumerate(ranked_r):
        lines.append(
            f"| {i+1} | {r['ts_code']} | {r.get('name','?')} | "
            f"{r['total_return']:+.2f}% | {r['annual_return']:+.2f}% | "
            f"{r['max_drawdown']:.2f}% | {r['sharpe']:.3f} | "
            f"{r['total_trades']} | {r['composite']:+.2f} |"
        )
    lines += ["", "## 详情"]
    for r in ranked_r:
        lines += [
            f"### {r['ts_code']} {r.get('name','?')}",
            f"- 网格: ¥{r['params'].price_lower:.4f} ~ ¥{r['params'].price_upper:.4f}",
            f"- 数据: {r.get('data_start','')} ~ {r.get('data_end','')} ({r['data_bars']} bars)",
            f"- 总收益: {r['total_return']:+.2f}% / 年化: {r['annual_return']:+.2f}%",
            f"- 回撤: {r['max_drawdown']:.2f}% / 夏普: {r['sharpe']:.3f}",
            f"- 成交: {r['total_trades']}笔 (买{r['buy_trades']}/卖{r['sell_trades']})",
            "",
        ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 报告: {out_dir}/report.md")


if __name__ == "__main__":
    main()
