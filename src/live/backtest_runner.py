"""Background backtest runner — wraps WizardConfig + run_pipeline in a thread."""

import matplotlib as _mpl
_mpl.use("Agg")  # Non-GUI backend for server environment

import threading
import uuid
from datetime import datetime as _dt
from pathlib import Path as _Path

_jobs: dict[str, dict] = {}  # job_id → {status, progress, step, result, ...}


def start_backtest(config: dict) -> str:
    """Start a backtest job in a background thread.

    Args:
        config: {
            index_codes: list, start_date: str, end_date: str,
            scheme_name: str | None, factors: list | None, weights: dict | None,
            initial_capital: float, top_n: int, rebalance_freq: str,
            risk_control_enabled: bool, stop_loss: float, take_profit: float,
            max_drawdown_stop: float, cooldown_days: int,
            position_sizing_method: str,
        }

    Returns:
        job_id string.
    """
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "status": "starting",
        "progress": 0,
        "step": "正在初始化...",
        "metrics": None,
        "result_dir": None,
        "error": None,
    }
    t = threading.Thread(target=_run, args=(job_id, config), daemon=True)
    t.start()
    return job_id


def get_status(job_id: str) -> dict | None:
    """Return current job status dict, or None if job not found."""
    return _jobs.get(job_id)


def _update(job_id: str, progress: int, step: str, **kw):
    if job_id in _jobs:
        _jobs[job_id].update({"progress": progress, "step": step, **kw})


def _run(job_id: str, config: dict):
    try:
        _update(job_id, 5, "加载数据...")
        from src.config import DATA_SOURCE, TUSHARE_TOKEN
        from src.data.cleaner import clean_pipeline
        from src.data.fetcher import (
            fetch_daily_basic, get_index_constituents, sync_stocks_data,
        )
        from src.data.industry import get_industry_map
        from src.data.storage import (
            load_daily_basic, load_daily_price, merge_fundamentals, save_daily_basic,
        )
        from src.factors.scorer import compute_total_score, select_top_n, standardize_factors
        from src.factors.ic_analyzer import compute_future_return, evaluate_factor
        from src.backtest import BacktestEngine
        from src.visualization.charts import generate_all_charts
        from src.visualization.backtest_charts import generate_backtest_charts
        from src.cli.wizard import WizardConfig

        cfg = WizardConfig()
        cfg.auto_confirm = True
        cfg.refresh_data = True
        cfg.do_scoring = True
        cfg.data_source = DATA_SOURCE

        # Apply user config
        cfg.index_codes = config.get("index_codes", ["000300"])
        cfg.start_date = config.get("start_date", "20240101")
        cfg.end_date = config.get("end_date", _dt.now().strftime("%Y%m%d"))
        # Factor data range (wider, for warmup). Falls back to backtest range.
        cfg.factor_start_date = config.get("factor_start_date", cfg.start_date)
        cfg.factor_end_date = config.get("factor_end_date", cfg.end_date)
        # Guard: factor range must cover backtest range, otherwise bt_df is empty.
        if cfg.factor_end_date < cfg.end_date:
            _update(job_id, 2, f"因子结束 ({cfg.factor_end_date}) < 回测结束 ({cfg.end_date})，自动扩展因子范围")
            cfg.factor_end_date = cfg.end_date
        if cfg.factor_start_date > cfg.start_date:
            _update(job_id, 2, f"因子起始 ({cfg.factor_start_date}) > 回测起始 ({cfg.start_date})，自动前移因子范围")
            cfg.factor_start_date = cfg.start_date
        cfg.initial_capital = config.get("initial_capital", 1_000_000)
        cfg.top_n = config.get("top_n", 20)
        cfg.rebalance_freq = config.get("rebalance_freq", "M")
        cfg.risk_control_enabled = config.get("risk_control_enabled", False)
        cfg.stop_loss = config.get("stop_loss", -0.15)
        cfg.take_profit = config.get("take_profit", 0.20)
        cfg.max_drawdown_stop = config.get("max_drawdown_stop", -0.20)
        cfg.cooldown_days = config.get("cooldown_days", 5)
        cfg.exclude_etf = config.get("exclude_etf", True)
        cfg.exclude_star = config.get("exclude_star", True)
        cfg.position_sizing_method = config.get("position_sizing_method", "equal_weight")

        # Scheme or manual factors
        scheme_name = config.get("scheme_name")
        if scheme_name:
            from src.scheme import load_scheme
            factors, weights = load_scheme(scheme_name)
            cfg.scheme_name = scheme_name
            cfg.enabled_factors = factors
            cfg.weights = dict(weights)
        else:
            cfg.enabled_factors = set(config.get("factors", []))
            cfg.weights = config.get("weights", {})

        # Step 1: Stock universe
        _update(job_id, 10, "获取股票列表...")
        all_ts_codes = []
        code2name = {}
        if "all" in cfg.index_codes:
            from src.data.fetcher import get_all_stocks
            constituents = get_all_stocks()
            all_ts_codes = constituents["ts_code"].tolist()
            code2name = dict(zip(constituents["ts_code"], constituents["name"]))
        else:
            for idx in cfg.index_codes:
                c = get_index_constituents(idx)
                all_ts_codes.extend(c["ts_code"].tolist())
                code2name.update(dict(zip(c["ts_code"], c["name"])))
        ts_codes = list(set(all_ts_codes))

        # Step 2: Load data — use factor date range for warmup and stock filtering
        _update(job_id, 20, "加载日线数据...")
        df = load_daily_price(start_date=cfg.factor_start_date, end_date=cfg.factor_end_date)
        if df.empty:
            raise RuntimeError("无日线数据")
        df = df[df["ts_code"].isin(ts_codes)]

        # Exclude ETFs if configured
        if cfg.exclude_etf:
            from src.grid.grid_etf import is_etf
            before = df["ts_code"].nunique()
            df = df[~df["ts_code"].apply(is_etf)]
            after = df["ts_code"].nunique()
            _update(job_id, 28, f"排除ETF: {before}→{after} 只")

        # Exclude STAR board if configured
        if cfg.exclude_star:
            before = df["ts_code"].nunique()
            df = df[~df["ts_code"].str.startswith("688")]
            after = df["ts_code"].nunique()
            _update(job_id, 29, f"排除科创板: {before}→{after} 只")

        # Clean
        _update(job_id, 30, "清洗数据...")
        df, _report = clean_pipeline(df, filter_stocks=True, end_date=cfg.end_date)

        # Step 5: Fundamentals
        _update(job_id, 40, "获取基本面数据...")
        basic_df = load_daily_basic(start_date=cfg.factor_start_date, end_date=cfg.factor_end_date)
        if basic_df.empty and cfg.data_source in ("tushare", "qmt"):
            basic_df = fetch_daily_basic(ts_codes, start_date=cfg.factor_start_date, end_date=cfg.factor_end_date)
            if not basic_df.empty:
                save_daily_basic(basic_df)
        if not basic_df.empty:
            df = merge_fundamentals(df, basic_df)

        # Step 5b: ROE data if needed (e.g. roe_yoy_rank, roe_rank)
        needs_fina = any(f in cfg.enabled_factors for f in ["roe_yoy_rank", "roe_rank", "roe_stability"])
        if needs_fina:
            _update(job_id, 42, "获取ROE数据...")
            from src.data.storage import load_fina_indicator, merge_fina_indicator
            from src.data.fetcher import fetch_fina_indicator
            # ROE data is quarterly; load from 1 year before backtest start for forward-fill
            fina_start = str(int(cfg.factor_start_date[:4]) - 1) + cfg.factor_start_date[4:] if len(cfg.factor_start_date) >= 8 else cfg.factor_start_date
            fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=fina_start, end_date=cfg.factor_end_date)
            if fina_df.empty and cfg.data_source in ("tushare", "qmt"):
                fina_df = fetch_fina_indicator(ts_codes, start_date=fina_start, end_date=cfg.factor_end_date)
            if not fina_df.empty and "roe" in fina_df.columns:
                df = merge_fina_indicator(df, fina_df)
            else:
                _update(job_id, 42, "ROE数据缺失，跳过roe相关因子", skip_roe=True)

        # Step 6: Factors
        _update(job_id, 50, "计算因子...")
        import src.factors.momentum
        import src.factors.volume
        import src.factors.volume_price
        import src.factors.volatility
        import src.factors.rsi
        import src.factors.ma_deviation
        import src.factors.turnover
        import src.factors.intraday_range
        import src.factors.valuation
        import src.factors.return_20d
        import src.factors.trend_60d
        import src.factors.roe_change
        import src.factors.grid_suitability
        import src.factors.minute_factors
        from src.factors.base import get_registered_factors

        # If any minute factors are enabled, pre-sync the factor date range to local
        # SQLite so factor computation reads locally instead of HTTP per-date.
        _minute_factors = [f for f in cfg.enabled_factors if f.endswith("_5m")]
        if _minute_factors and not df.empty:
            from src.data.qmt_fetcher import sync_minute_kline, get_minute_stats
            # Clamp start to earliest available minute data (Windows has ~2025-07+)
            minute_info = get_minute_stats()
            clamp_start = minute_info.get("earliest_date", cfg.factor_start_date) or cfg.factor_start_date
            sync_start = max(cfg.factor_start_date, clamp_start)
            _update(job_id, 52, f"预同步分钟数据 ({sync_start}~{cfg.factor_end_date})...")
            sync_minute_kline(end_date=cfg.factor_end_date, start_date=sync_start)

        all_factors = get_registered_factors()
        factor_cols = []
        for name in sorted(cfg.enabled_factors):
            if name in all_factors:
                factor = all_factors[name]()
                df = factor.calculate(df)
                factor_cols.append(factor.factor_name)

        # Step 7: Score
        _update(job_id, 65, "标准化+打分...")
        df = standardize_factors(df, factor_cols)
        df = compute_total_score(df, weights=cfg.weights)

        # Fill default weights
        from src.factors.scorer import _factor_to_score_col
        from src.config import DEFAULT_WEIGHTS
        for name in cfg.enabled_factors:
            score_col = _factor_to_score_col(name)
            if score_col not in cfg.weights:
                cfg.weights[score_col] = DEFAULT_WEIGHTS.get(score_col, 0.0)

        top_picks = select_top_n(df, df["trade_date"].max(), n=cfg.top_n)

        # Step 8: Backtest — trim to user's backtest window after factor warmup
        _update(job_id, 75, "运行回测...")
        bt_start = config.get("start_date", cfg.start_date)
        bt_end = config.get("end_date", cfg.end_date)
        bt_df = df[(df["trade_date"] >= bt_start) & (df["trade_date"] <= bt_end)]
        if "total_score" not in bt_df.columns:
            bt_df = bt_df.copy()
            bt_df["total_score"] = 1.0

        engine = BacktestEngine(
            initial_capital=cfg.initial_capital,
            top_n=cfg.top_n,
            rebalance_freq=cfg.rebalance_freq,
            risk_control_enabled=cfg.risk_control_enabled,
            stop_loss=cfg.stop_loss,
            take_profit=cfg.take_profit,
            max_drawdown_stop=cfg.max_drawdown_stop,
            cooldown_days=cfg.cooldown_days,
            position_sizing_method=cfg.position_sizing_method,
        )

        from src.data.fetcher import get_index_daily
        bm_symbol = cfg.index_codes[0] if cfg.index_codes and "all" not in cfg.index_codes else "000300"
        bm_ts = bm_symbol if "." in bm_symbol else f"{bm_symbol}.SH"
        benchmark_df = get_index_daily(
            ts_code=bm_ts,
            start_date=cfg.factor_start_date,
            end_date=cfg.factor_end_date,
        )

        bt_result = engine.run(bt_df, benchmark_df=benchmark_df)

        # Step 9: Charts + Save
        _update(job_id, 90, "生成报告...")
        from src.config import DATA_DIR
        result_dir = DATA_DIR / "results" / (_dt.now().strftime("%Y%m%d_%H%M%S") + "_backtest")
        result_dir.mkdir(parents=True, exist_ok=True)
        from src.visualization.charts import generate_all_charts
        from src.visualization.backtest_charts import generate_backtest_charts
        generate_all_charts(df, {}, factor_cols, code2name, weights=cfg.weights, output_dir=result_dir)
        generate_backtest_charts(bt_result, output_dir=result_dir)

        # Build markdown report matching CLI format
        m = bt_result.metrics
        report_lines = [
            "# 回测报告",
            f"**生成时间**: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**回测区间**: {bt_start} ~ {bt_end}",
            "",
            "## 参数配置",
            f"- 方案: {config.get('scheme_name', '自定义')}",
            f"- 指数: {', '.join(config.get('index_codes', []))}",
            f"- 初始资金: {config.get('initial_capital', 0):,.0f}",
            f"- 持仓Top N: {config.get('top_n', 0)}",
            f"- 调仓频率: {config.get('rebalance_freq', 'M')}",
            f"- 仓位管理: {config.get('position_sizing_method', 'equal_weight')}",
            f"- 风控: {'启用' if config.get('risk_control_enabled') else '未启用'}",
            f"- 排除ETF: {'是' if config.get('exclude_etf', True) else '否'}",
            f"- 排除科创板: {'是' if config.get('exclude_star', True) else '否'}",
            "",
            "## 回测指标",
            f"- 年化收益率: {m.get('annual_return', 0):.2%}",
            f"- 累计收益率: {m.get('total_return', 0):.2%}",
            f"- 最大回撤: {m.get('max_drawdown', 0):.2%}",
            f"- 夏普比率: {m.get('sharpe_ratio', 0):.2f}",
            f"- Calmar比率: {m.get('calmar_ratio', 0):.2f}",
            f"- 胜率: {m.get('win_rate', 0):.1%}",
            f"- 总交易笔数: {m.get('trade_count', m.get('total_trades', 0))}",
            f"- 回测天数: {m.get('n_days', 0)}",
            f"- 期末净值: {m.get('final_nav', 0):,.2f}",
            "",
            f"## 图表",
        ]
        # List generated charts
        for png in sorted(result_dir.glob("*.png")):
            name = png.stem.replace("backtest_", "").replace("_", " ").title()
            report_lines.append(f"- [{name}]({png.name})")
        report_lines.append("")
        # Collapsible trade details
        report_lines.append("<details>")
        report_lines.append("<summary>成交明细（点击展开）</summary>")
        report_lines.append("")
        if not bt_result.trades.empty:
            report_lines.append("| # | 日期 | 股票 | 方向 | 价格 | 数量 | 金额 |")
            report_lines.append("|---|---|---|---|---|---|---|")
            for i, (_, t) in enumerate(bt_result.trades.iterrows(), 1):
                report_lines.append(
                    f"| {i} | {t.get('date', '-')} | {t.get('ts_code', '-')} | "
                    f"{t.get('action', '-')} | {t.get('price', 0):.2f} | "
                    f"{int(t.get('shares', 0))} | {t.get('amount', 0):.0f} |"
                )
        report_lines.append("")
        report_lines.append("</details>")
        (result_dir / "report.md").write_text("\n".join(report_lines), encoding="utf-8")

        # Step 10: Done — persist to history
        import json as _json
        from src.data.storage import save_backtest_job
        try:
            save_backtest_job(
                scheme_name=config.get("scheme_name", "自定义"),
                config_json=_json.dumps(config, ensure_ascii=False),
                metrics_json=_json.dumps(bt_result.metrics, ensure_ascii=False),
                result_dir=str(result_dir),
            )
        except Exception:
            pass  # non-critical, don't fail the whole backtest

        result_url = f"/dashboard/results/{result_dir.name}"
        _update(job_id, 100, "完成", status="done",
                metrics=bt_result.metrics,
                result_dir=str(result_dir),
                result_url=result_url)

    except Exception as e:
        _update(job_id, 0, f"错误: {e}", status="error", error=str(e))
