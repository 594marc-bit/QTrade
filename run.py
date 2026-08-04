#!/usr/bin/env python3
"""QTrade interactive wizard entry point.

Usage:
    # Interactive mode (no args) — step-by-step wizard
    python run.py

    # Command-line mode — run directly with specified params
    python run.py --index 000300 --start 20240101 --factors momentum_20d,rsi_14d

    # Mixed mode — some params from CLI, rest via interactive prompts
    python run.py --index 000300 --capital 500000
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Disable proxy
import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)


def _run_grid_interactive(args, grid_kwargs_fn):
    """Interactive grid trading wizard: step-by-step config → backtest or live signals."""
    import datetime as _dt
    from src.cli.prompts import confirm, input_value, select
    from src.config import DATA_DIR
    from src.config import (
        GRID_BASE_SHARES, GRID_LEVELS, GRID_MODE, GRID_ORDER_SHARES, GRID_PRICE_RANGE_PCT,
    )

    print("\n" + "=" * 60)
    print("  网格交易 — 交互式配置")
    print("=" * 60)

    # Step 1: Stock selection
    print("\n[1/6] 股票选择")
    choice = select(
        "选择股票范围",
        [
            "手动输入股票代码",
            "因子筛选 (grid_suitability 自动选出适合网格的股票)",
            "因子筛选 - 仅沪深ETF",
            "使用默认测试股票 (000001.SZ)",
        ],
        default=4,
    )
    use_factor_select = False
    use_etf_universe = False
    n = 5  # default top-N for factor screening
    if choice == 1:
        raw = input_value("股票代码 (逗号分隔，如 000001.SZ,510050.SH)", default="000001.SZ")
        stock_codes = [s.strip() for s in raw.split(",")]
    elif choice == 2:
        use_factor_select = True
        n = int(input_value("选取 top N 只股票", default="5"))
        stock_codes = None
    elif choice == 3:
        use_factor_select = True
        use_etf_universe = True
        n = int(input_value("选取 top N 只 ETF", default="5"))
        stock_codes = None
        print("  范围: 仅沪深ETF (51xxxx.SH + 159xxx.SZ)")
    else:
        stock_codes = ["000001.SZ"]
    if not use_factor_select:
        print(f"  已选: {', '.join(stock_codes)}")

    # Step 2: Mode
    print("\n[2/6] 运行模式")
    mode_choice = select(
        "选择模式",
        ["回测 (分钟K线)", "实盘信号生成"],
        default=1,
    )
    is_live = mode_choice == 2

    # Step 3: Date range (backtest only)
    start_date = end_date = ""
    if not is_live:
        print("\n[3/6] 回测日期范围")
        default_start = (_dt.date.today() - _dt.timedelta(days=365)).strftime("%Y%m%d")
        default_end = _dt.date.today().strftime("%Y%m%d")
        start_date = input_value("起始日期 (YYYYMMDD)", default=default_start)
        end_date = input_value("结束日期 (YYYYMMDD)", default=default_end)
        print(f"  区间: {start_date} ~ {end_date}")
    else:
        print("\n[3/6] 实盘模式 — 使用最新收盘价，无需选择日期范围")
        start_date = _dt.date.today().strftime("%Y%m%d")
        end_date = start_date

    # Step 4: Grid parameters
    print("\n[4/6] 网格参数")
    price_pct = float(input_value("价格区间 ±%", default=str(GRID_PRICE_RANGE_PCT)))
    levels = int(input_value("网格层数", default=str(GRID_LEVELS)))
    mode = "equal" if select("间距模式", ["等间距 (equal)", "等比间距 (ratio)"], default=1 if GRID_MODE == "equal" else 2) == 1 else "ratio"
    shares = int(input_value("每层交易股数", default=str(GRID_ORDER_SHARES)))
    base = int(input_value("初始底仓股数 (0=裸网格)", default=str(GRID_BASE_SHARES)))
    capital = float(input_value("初始资金", default="200000"))

    print(f"  配置: ±{price_pct}%, {levels}层, {mode}, {shares}股/层, 底仓{base}股, 资金{capital:,.0f}")

    # Pack overrides
    overrides = {
        "price_range_pct": price_pct,
        "grid_levels": levels,
        "grid_mode": mode,
        "order_shares": shares,
        "base_shares": base,
    }

    # Resolve factor-selected stocks (needs date range from Step 3)
    if use_factor_select:
        from src.data.storage import load_daily_price as _ldp2
        from src.grid.grid_stock_selector import select_stocks
        eff_range = "沪深ETF" if use_etf_universe else "全A股"
        print(f"\n  计算 grid_suitability 因子 ({eff_range}, {start_date} ~ {end_date})...")
        df_daily = _ldp2(start_date=start_date, end_date=end_date)
        if df_daily.empty:
            print("  错误: 无日线数据，无法计算因子")
            return
        if use_etf_universe:
            from src.grid.grid_etf import filter_etf_universe
            df_daily = filter_etf_universe(df_daily)
            if df_daily.empty:
                print("  错误: 无ETF数据")
                return
            print(f"  ETF范围: {df_daily['ts_code'].nunique()} 只")
        import src.factors.grid_suitability  # noqa: triggers registration
        from src.factors.base import get_registered_factors
        factor_cls = get_registered_factors().get("grid_suitability")
        if factor_cls:
            df_daily = factor_cls().calculate(df_daily)
        stock_codes = select_stocks(factor_top_n=n, df=df_daily)
        if not stock_codes:
            print(f"  错误: 因子筛选无结果，请检查数据范围")
            return
        print(f"  因子筛选 top-{n}: {', '.join(stock_codes)}")

    # Step 5: Confirm
    print("\n[5/6] 确认")
    if not confirm("确认配置并开始？", default=True):
        print("已取消")
        return

    # Step 6: Run
    print("\n[6/6] 执行...")

    from src.data.storage import load_daily_price as _ldp
    from src.grid.grid_backtest import GridBacktestEngine
    from src.grid.grid_stock_selector import build_grid_params
    from src.grid.grid_signal_generator import GridSignalGenerator

    if is_live:
        # Live signal generation
        df = _ldp(ts_codes=stock_codes, start_date=(_dt.date.today() - _dt.timedelta(days=30)).strftime("%Y%m%d"), end_date=end_date)
        if df.empty:
            print("错误: 无日线数据")
            return
        for ts_code in stock_codes:
            stock_df = df[df["ts_code"] == ts_code].sort_values("trade_date")
            if stock_df.empty:
                print(f"  {ts_code}: 无数据，跳过")
                continue
            latest_close = float(stock_df["close"].iloc[-1])
            latest_date = stock_df["trade_date"].iloc[-1]
            try:
                params = build_grid_params(ts_code, df, **overrides)
            except ValueError:
                print(f"  {ts_code}: 无法生成网格参数，跳过")
                continue
            gen = GridSignalGenerator(ts_code, params)
            signals = gen.generate_signals(latest_close, latest_date)
            if signals:
                saved = gen.save_signals(signals)
                print(f"  {ts_code}: 生成 {saved} 条网格信号 (最新价={latest_close})")
                for s in signals:
                    print(f"    {s['action']} {s['quantity']}股 @{s.get('limit_price', 'MKT')}")
            else:
                print(f"  {ts_code}: 无新网格信号 (最新价={latest_close})")
    else:
        # Backtest
        daily_df = _ldp(ts_codes=stock_codes, start_date=start_date, end_date=end_date)
        from src.data.qmt_fetcher import fetch_minute_kline
        from src.data.storage import load_minute_range
        from src.grid.grid_report import generate_report

        out_dir = DATA_DIR / "results" / (_dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_grid")
        out_dir.mkdir(parents=True, exist_ok=True)

        results = {}
        params_map = {}
        for ts_code in stock_codes:
            print(f"\n  {ts_code}: 拉取5分钟K线数据...")
            # Local SQLite first, fallback to Windows HTTP
            minute_df = load_minute_range(ts_code, start_date, end_date)
            if minute_df.empty:
                minute_df = fetch_minute_kline(ts_code, start_date, end_date)
            if minute_df.empty and not daily_df.empty:
                stock_daily = daily_df[daily_df["ts_code"] == ts_code]
                if not stock_daily.empty:
                    minute_df = stock_daily.rename(columns={"trade_date": "bar_time"})
            if minute_df.empty:
                print(f"    {ts_code}: 无数据，跳过")
                continue
            try:
                params = build_grid_params(ts_code, daily_df if not daily_df.empty else None, **overrides)
            except ValueError:
                print(f"    {ts_code}: 无法计算网格参数，跳过")
                continue
            params_map[ts_code] = params
            engine = GridBacktestEngine(initial_capital=capital)
            result = engine.run(minute_df, params)
            results[ts_code] = result
            print(f"    {result.summary}")
            print(f"    数据: {len(minute_df)} bars, {len(result.trades)} trades")

        if results:
            generate_report(results, params_map, start_date, end_date, out_dir)
        print(f"\n报告: {out_dir}/report.md")


def _run_grid_mode(args):
    """Handle --mode grid: grid trading backtest, live signals, and reports."""
    import datetime
    from pathlib import Path

    from src.config import DATA_DIR
    from src.data.storage import load_daily_price
    from src.grid.grid_backtest import GridBacktestEngine
    from src.grid.grid_params import GridParams
    from src.grid.grid_stock_selector import build_grid_params, select_stocks

    # Grid param overrides from CLI (None = use config.ini defaults)
    def _grid_kwargs():
        kw = {}
        if getattr(args, "grid_price_pct", None) is not None:
            kw["price_range_pct"] = args.grid_price_pct
        if getattr(args, "grid_levels", None) is not None:
            kw["grid_levels"] = args.grid_levels
        if getattr(args, "grid_mode", None) is not None:
            kw["grid_mode"] = args.grid_mode
        if getattr(args, "grid_shares", None) is not None:
            kw["order_shares"] = args.grid_shares
        if getattr(args, "grid_base_shares", None) is not None:
            kw["base_shares"] = args.grid_base_shares
        return kw

    # --- Interactive mode (no --grid-stocks, no --grid-live) ---
    has_stock_source = args.grid_stocks or args.grid_factor
    if not has_stock_source and not args.grid_live:
        _run_grid_interactive(args, _grid_kwargs)
        return

    # --- Live signal generation mode ---
    if args.grid_live:
        if not args.grid_stocks:
            print("错误: --grid-live 需要指定 --grid-stocks")
            return
        stock_codes = [s.strip() for s in args.grid_stocks.split(",")]
        end_date = datetime.date.today().strftime("%Y%m%d")
        start_date = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d")

        from src.data.storage import load_daily_price as _ldp
        df = _ldp(ts_codes=stock_codes, start_date=start_date, end_date=end_date)
        if df.empty:
            print("错误: 无日线数据")
            return

        from src.grid.grid_params import GridParams
        from src.grid.grid_signal_generator import GridSignalGenerator

        for ts_code in stock_codes:
            stock_df = df[df["ts_code"] == ts_code].sort_values("trade_date")
            if stock_df.empty:
                print(f"  {ts_code}: 无数据，跳过")
                continue
            latest_close = float(stock_df["close"].iloc[-1])
            latest_date = stock_df["trade_date"].iloc[-1]
            try:
                params = build_grid_params(ts_code, df, **_grid_kwargs())
            except ValueError:
                print(f"  {ts_code}: 无法生成网格参数，跳过")
                continue

            gen = GridSignalGenerator(ts_code, params)
            signals = gen.generate_signals(latest_close, latest_date)
            if signals:
                saved = gen.save_signals(signals)
                print(f"  {ts_code}: 生成 {saved} 条网格信号 (最新价={latest_close}, 日期={latest_date})")
                for s in signals:
                    print(f"    {s['action']} {s['ts_code']} {s['quantity']}股 @{s.get('limit_price', 'MKT')} [{s.get('remark', '')}]")
            else:
                print(f"  {ts_code}: 无新网格信号 (最新价={latest_close}, 日期={latest_date})")
        return

    # Determine stocks
    stock_codes = None
    if args.grid_stocks:
        stock_codes = [s.strip() for s in args.grid_stocks.split(",")]
    elif args.grid_factor:
        # Use recent daily data for factor screening
        end_date = args.grid_end or datetime.date.today().strftime("%Y%m%d")
        start_date = args.grid_start or "20240101"
        df = load_daily_price(start_date=start_date, end_date=end_date)
        if args.grid_etf:
            from src.grid.grid_etf import filter_etf_universe
            df = filter_etf_universe(df)
            print(f"ETF范围: {df['ts_code'].nunique()} 只")
        if not df.empty:
            # Import grid suitability factor and compute
            import src.factors.grid_suitability  # noqa: triggers registration
            from src.factors.base import get_registered_factors
            factor_cls = get_registered_factors().get("grid_suitability")
            if factor_cls:
                factor = factor_cls()
                df = factor.calculate(df)
            stock_codes = select_stocks(
                factor_top_n=args.grid_top, df=df,
            )
            print(f"因子筛选 top-{args.grid_top}: {stock_codes}")
        else:
            print("错误: 无日线数据，无法进行因子筛选。请先同步数据。")
            return

    if not stock_codes:
        print("错误: 请指定 --grid-stocks 或 --grid-factor")
        return

    # Grid backtest date range: default to latest 1 year of minute data
    end_date = args.grid_end or datetime.date.today().strftime("%Y%m%d")
    start_date = args.grid_start or (
        datetime.date.today() - datetime.timedelta(days=365)
    ).strftime("%Y%m%d")
    print(f"网格回测区间: {start_date} ~ {end_date}")

    # Load daily data for grid param auto-calculation (center price from recent close)
    daily_df = load_daily_price(ts_codes=stock_codes, start_date=start_date, end_date=end_date)

    # Run grid backtest per stock using minute K-line data
    out_dir = DATA_DIR / "results" / (datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_grid")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Import here to avoid top-level dependency on qmt_fetcher
    from src.data.qmt_fetcher import fetch_minute_kline
    from src.data.storage import load_minute_range

    results = {}
    params_map = {}
    for ts_code in stock_codes:
        print(f"\n  {ts_code}: 拉取5分钟K线数据...")
        # Local SQLite first, fallback to Windows HTTP
        minute_df = load_minute_range(ts_code, start_date, end_date)
        if minute_df.empty:
            minute_df = fetch_minute_kline(ts_code, start_date, end_date)
        if minute_df.empty:
            print(f"    {ts_code}: 无分钟数据，尝试使用日线...")
            if not daily_df.empty:
                stock_daily = daily_df[daily_df["ts_code"] == ts_code]
                if stock_daily.empty:
                    print(f"    {ts_code}: 跳过")
                    continue
                minute_df = stock_daily.rename(columns={"trade_date": "bar_time"})
            else:
                continue

        try:
            params = build_grid_params(ts_code, daily_df if not daily_df.empty else None, **_grid_kwargs())
        except ValueError:
            print(f"    {ts_code}: 无法计算网格参数，跳过")
            continue
        params_map[ts_code] = params

        engine = GridBacktestEngine(
            initial_capital=float(args.capital or 1_000_000)
        )
        result = engine.run(minute_df, params)
        results[ts_code] = result
        print(f"    {result.summary}")
        print(f"    {ts_code} 数据: {len(minute_df)} bars, {len(result.trades)} trades")

    if not results:
        print("无有效回测结果")
        return

    # Generate full report with charts and trade details
    from src.grid.grid_report import generate_report
    generate_report(results, params_map, start_date, end_date, out_dir)
    print(f"\n报告: {out_dir}/report.md")


def main():
    parser = argparse.ArgumentParser(
        description="QTrade 量化交易系统 — 交互式向导",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python run.py                                    # 交互式向导\n"
            "  python run.py --index 000300 --start 20240101    # 部分参数\n"
            "  python run.py --index 000300 --start 20240101 --end 20250101 \\\n"
            "       --source akshare --factors momentum_20d,rsi_14d \\\n"
            "       --capital 1000000 --top-n 20 --rebalance M\n"
        ),
    )

    parser.add_argument("--index", type=str, help="股票指数代码，多选用逗号分隔 (000300,000905,000852,all)")
    parser.add_argument("--stocks", type=str, help="自定义股票代码，逗号分隔 (如 600519,000858,601318)")
    parser.add_argument("--start", type=str, help="因子计算起始日期 (YYYYMMDD)，数据会自动加载更早范围做预热")
    parser.add_argument("--end", type=str, help="因子计算结束日期 (YYYYMMDD)")
    parser.add_argument("--source", type=str, choices=["akshare", "tushare", "qmt"], help="数据源")
    parser.add_argument("--factors", type=str, help="启用因子，逗号分隔 (如 momentum_20d,rsi_14d)")
    parser.add_argument("--capital", type=float, help="初始资金")
    parser.add_argument("--top-n", type=int, help="持仓数量")
    parser.add_argument("--rebalance", type=str, choices=["M", "W", "Q"], help="调仓频率")
    parser.add_argument("--no-risk-control", action="store_true", help="禁用风控")
    parser.add_argument("--stop-loss", type=float, help="个股止损阈值 (如 -0.08)")
    parser.add_argument("--take-profit", type=float, help="个股止盈阈值 (如 0.15)")
    parser.add_argument("--max-drawdown", type=float, help="组合回撤止损 (如 -0.10)")
    parser.add_argument("--cooldown-days", type=int, help="止损后冷冻天数")
    parser.add_argument("--industry-neutral", action="store_true", help="启用行业中性约束")
    parser.add_argument("--max-industry-pct", type=float, help="单行业持仓上限 (如 0.30)")
    parser.add_argument("--position-sizing", type=str,
                        choices=["equal_weight", "score_weighted", "risk_parity"],
                        help="仓位管理方式")
    parser.add_argument("--scheme", type=str, help="方案名称 (从 schemes.yaml 加载因子和权重)")
    parser.add_argument("--backtest-start", type=str, help="回测起始日期 (YYYYMMDD)，默认使用数据全量范围")
    parser.add_argument("--backtest-end", type=str, help="回测结束日期 (YYYYMMDD)，默认使用数据全量范围")
    parser.add_argument("--no-sync", action="store_true", help="跳过数据同步，直接使用缓存数据")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过所有确认提示，自动执行回测")
    # Grid mode
    parser.add_argument("--mode", type=str, choices=["factor", "grid"], default="factor",
                        help="运行模式: factor (多因子选股, 默认) / grid (网格交易)")
    parser.add_argument("--grid-stocks", type=str, help="网格模式: 指定股票代码 (如 000001.SZ,600519.SH)")
    parser.add_argument("--grid-factor", action="store_true", help="网格模式: 因子筛选适合网格的股票")
    parser.add_argument("--grid-top", type=int, default=5, help="网格模式: 因子筛选的 top N 数量")
    parser.add_argument("--grid-start", type=str, help="网格回测起始日期 (YYYYMMDD)")
    parser.add_argument("--grid-end", type=str, help="网格回测结束日期 (YYYYMMDD)")
    parser.add_argument("--grid-live", action="store_true", help="网格模式: 实盘信号生成")
    parser.add_argument("--grid-report", action="store_true", help="网格模式: 生成实盘/回测报告")
    parser.add_argument("--grid-price-pct", type=float, help="网格价格区间: 基准价±百分比，默认15")
    parser.add_argument("--grid-levels", type=int, help="网格层数，默认10")
    parser.add_argument("--grid-mode", type=str, choices=["equal", "ratio"], help="网格间距: equal(等距)/ratio(等比)，默认ratio")
    parser.add_argument("--grid-shares", type=int, help="每层交易股数，默认1000")
    parser.add_argument("--grid-base-shares", type=int, help="初始底仓股数，默认0")
    parser.add_argument("--grid-etf", action="store_true", help="网格模式: 仅沪深ETF范围")

    args = parser.parse_args()

    # --stocks and --index are mutually exclusive
    if args.index and args.stocks:
        parser.error("不能同时指定 --index 和 --stocks")

    # Build CLI args dict (only non-None values)
    cli_args = {k: v for k, v in vars(args).items() if v is not None}

    # Grid mode: dispatch to grid handler
    if cli_args.get("mode") == "grid":
        _run_grid_mode(args)
        return

    # Determine mode — scheme or stocks can also enable full CLI mode
    has_any_arg = bool(cli_args)
    has_scheme = "scheme" in cli_args
    has_stocks = "stocks" in cli_args
    has_stock_source = "index" in cli_args or has_stocks
    # Full CLI with --start/--end/--source/--scheme but no --index → all-stock universe
    has_full_params = (
        all(k in cli_args for k in ["start", "end", "source"])
        and ("factors" in cli_args or has_scheme or has_stocks)
    )
    is_full_cli = has_full_params and (has_stock_source or cli_args.get("yes"))

    if is_full_cli:
        # Full command-line mode — no interaction needed
        from src.cli.wizard import WizardConfig, run_pipeline
        from src.scheme import load_scheme, list_schemes

        cfg = WizardConfig()

        # Stock source: --stocks or --index
        if has_stocks:
            from src.cli.wizard import parse_stock_codes
            cfg.stock_codes = parse_stock_codes(cli_args["stocks"])
            if has_scheme or "factors" in cli_args:
                print("提示: 自定义股票池模式下忽略 --scheme/--factors，直接等权回测")
        else:
            idx_val = cli_args.get("index", "all")
            cfg.index_codes = [i.strip() for i in idx_val.split(",") if i.strip()]
        cfg.start_date = cli_args["start"]
        cfg.end_date = cli_args["end"]
        cfg.data_source = cli_args["source"]

        # Load scheme if specified (skip for custom stock pool)
        scheme_factors = None
        scheme_weights = None
        if has_scheme and not has_stocks:
            scheme_name = cli_args["scheme"]
            try:
                scheme_factors, scheme_weights = load_scheme(scheme_name)
                cfg.scheme_name = scheme_name
            except ValueError as e:
                print(f"错误: {e}")
                sys.exit(1)

        # Factors: --factors takes priority over scheme (skip for custom stock pool)
        if has_stocks:
            cfg.do_scoring = False
        elif "factors" in cli_args:
            cfg.enabled_factors = set(cli_args["factors"].split(","))
        elif scheme_factors is not None:
            cfg.enabled_factors = scheme_factors
        else:
            cfg.enabled_factors = set()

        # Initialize weights: scheme weights first, then defaults for missing
        from src.factors.scorer import _factor_to_score_col
        from src.config import DEFAULT_WEIGHTS

        if scheme_weights is not None:
            cfg.weights = dict(scheme_weights)
        # Fill in missing weights from defaults
        for name in cfg.enabled_factors:
            score_col = _factor_to_score_col(name)
            if score_col not in cfg.weights:
                cfg.weights[score_col] = DEFAULT_WEIGHTS.get(score_col, 0.0)

        if "capital" in cli_args:
            cfg.initial_capital = cli_args["capital"]
        if "top_n" in cli_args:
            cfg.top_n = cli_args["top_n"]
        if "rebalance" in cli_args:
            cfg.rebalance_freq = cli_args["rebalance"]
        if "position_sizing" in cli_args:
            cfg.position_sizing_method = cli_args["position_sizing"]
        if cli_args.get("no_risk_control"):
            cfg.risk_control_enabled = False

        # Risk control params
        risk_params = ["stop_loss", "take_profit", "max_drawdown_stop", "cooldown_days"]
        has_risk_arg = any(cli_args.get(p.replace("max_drawdown_stop", "max_drawdown")) is not None for p in risk_params)
        if has_risk_arg and not cli_args.get("no_risk_control"):
            cfg.risk_control_enabled = True
        if "stop_loss" in cli_args:
            cfg.stop_loss = cli_args["stop_loss"]
        if "take_profit" in cli_args:
            cfg.take_profit = cli_args["take_profit"]
        if "max_drawdown" in cli_args:
            cfg.max_drawdown_stop = cli_args["max_drawdown"]
        if "cooldown_days" in cli_args:
            cfg.cooldown_days = cli_args["cooldown_days"]

        # Industry neutral
        if "industry_neutral" in cli_args:
            cfg.industry_neutral_enabled = True
        if "max_industry_pct" in cli_args:
            cfg.max_industry_pct = cli_args["max_industry_pct"]

        # Backtest date range
        if "backtest_start" in cli_args:
            cfg.backtest_start = cli_args["backtest_start"]
        if "backtest_end" in cli_args:
            cfg.backtest_end = cli_args["backtest_end"]

        # Auto-confirm
        if cli_args.get("yes"):
            cfg.auto_confirm = True

        # Skip data sync
        if cli_args.get("no_sync"):
            cfg.refresh_data = False

        run_pipeline(cfg)
    else:
        # Interactive or mixed mode
        from src.cli.wizard import run_wizard
        run_wizard(cli_args if has_any_arg else None)


if __name__ == "__main__":
    main()
