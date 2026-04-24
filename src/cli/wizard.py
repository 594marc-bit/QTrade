"""Interactive wizard that guides users through the QTrade pipeline step by step."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rich.console import Console

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Disable system proxy before any imports that use requests
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

from src.cli.prompts import confirm, input_value, multi_select, select
from src.cli.display import (
    show_banner,
    show_backtest_summary,
    show_data_refresh_info,
    show_factor_table,
    show_param_summary,
    show_top_stocks,
)
from src.config import (
    DATA_DIR,
    DEFAULT_WEIGHTS,
    PROJECT_ROOT,
)
from src.factors.base import get_registered_factors
from src.factors.scorer import _factor_to_score_col
from src.scheme import list_schemes, load_scheme, save_scheme

# Import all factor modules to trigger registration
import src.factors.momentum
import src.factors.volume
import src.factors.volatility
import src.factors.rsi
import src.factors.ma_deviation
import src.factors.turnover
import src.factors.intraday_range
import src.factors.valuation
import src.factors.return_20d
import src.factors.trend_60d

console = Console()

# Available indices
INDEX_OPTIONS = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "all": "全部A股",
}


class WizardConfig:
    """Holds all configuration collected by the wizard."""

    def __init__(self):
        self.index_code: str = "000300"
        self.start_date: str = "20230101"
        self.end_date: str = date.today().strftime("%Y%m%d")
        self.refresh_data: bool = True
        self.data_source: str = "tushare"
        self.enabled_factors: set[str] = {"intraday_range_10d", "pb_rank", "pe_ttm_rank", "trend_60d", "volatility_20d"}
        self.weights: dict[str, float] = {}
        self.do_scoring: bool = True
        self.initial_capital: float = 1_000_000
        self.top_n: int = 20
        self.rebalance_freq: str = "M"
        self.risk_control_enabled: bool = False
        self.stop_loss: float = -0.12
        self.take_profit: float = 0.15
        self.max_drawdown_stop: float = -0.10
        self.cooldown_days: int = 5
        self.position_sizing_method: str = "equal_weight"
        self.min_weight: float = 0.05
        self.max_weight: float = 0.20
        self.industry_neutral_enabled: bool = False
        self.max_industry_pct: float = 0.30
        self.backtest_start: str | None = None
        self.backtest_end: str | None = None
        self.auto_confirm: bool = False


def step_index(cfg: WizardConfig) -> None:
    """Step 1: Select stock universe."""
    options = [f"{v} ({k})" for k, v in INDEX_OPTIONS.items()]
    default_idx = list(INDEX_OPTIONS.keys()).index(cfg.index_code) + 1
    choice = select("选择股票范围", options, default=default_idx)
    cfg.index_code = list(INDEX_OPTIONS.keys())[choice - 1]
    console.print(f"  [green]已选择: {INDEX_OPTIONS[cfg.index_code]}[/green]")


def step_date_range(cfg: WizardConfig) -> None:
    """Step 2: Select date range."""
    console.print("\n[bold cyan]配置数据时间范围[/]")
    raw_start = input_value("起始日期 (YYYYMMDD)", default=cfg.start_date)
    raw_end = input_value("结束日期 (YYYYMMDD)", default=cfg.end_date)

    # Validate format
    for label, val in [("起始日期", raw_start), ("结束日期", raw_end)]:
        try:
            datetime.strptime(val, "%Y%m%d")
        except ValueError:
            console.print(f"[red]{label} 格式无效，使用默认值[/red]")
            return

    cfg.start_date = raw_start
    cfg.end_date = raw_end
    console.print(f"  [green]时间范围: {cfg.start_date} ~ {cfg.end_date}[/green]")


def step_data_refresh(cfg: WizardConfig) -> None:
    """Step 3: Check data freshness and decide whether to refresh."""
    from src.data.storage import get_connection

    try:
        conn = get_connection()
        result = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
        count_result = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM daily_price").fetchone()
        conn.close()
        latest_date = result[0] if result and result[0] else None
        stock_count = count_result[0] if count_result else 0
    except Exception:
        latest_date = None
        stock_count = 0

    console.print("\n[bold cyan]数据刷新[/]")
    show_data_refresh_info(latest_date, stock_count)

    if latest_date is None:
        cfg.refresh_data = True
        console.print("  [yellow]无缓存数据，将自动获取[/yellow]")
        return

    cfg.refresh_data = confirm("是否刷新最新数据？", default=True)


def step_data_source(cfg: WizardConfig) -> None:
    """Step 4: Select data source."""
    from src.config import TUSHARE_TOKEN

    options = ["AKShare (免费，无需Token)", "Tushare (需要Token，更稳定)"]
    default = 1 if cfg.data_source == "akshare" else 2
    choice = select("选择数据源", options, default=default)

    if choice == 2:
        if not TUSHARE_TOKEN:
            console.print("  [red]未检测到 Tushare Token！[/red]")
            console.print("  [dim]请在 .env 文件中配置 TUSHARE_TOKEN[/dim]")
            console.print("  [dim]将在 .env 中使用: TUSHARE_TOKEN=your_token_here[/dim]")
            if not confirm("仍要使用 Tushare？", default=False):
                choice = 1
        cfg.data_source = "tushare" if choice == 2 else "akshare"
    else:
        cfg.data_source = "akshare"

    console.print(f"  [green]数据源: {cfg.data_source}[/green]")


def step_scheme_selection(cfg: WizardConfig) -> bool:
    """Step 5: Load a scheme (factor+weight preset).

    Returns True if a scheme was loaded, False if skipped.
    If True, cfg.enabled_factors and cfg.weights are populated.
    """
    from src.scheme import ensure_schemes_file

    ensure_schemes_file()
    schemes = list_schemes()

    if not schemes:
        console.print("  [dim]无可用方案，将手动配置因子和权重[/dim]")
        return False

    console.print("\n[bold cyan]方案选择[/]")
    console.print("  [dim]可从已保存的方案中加载因子和权重配置[/dim]")

    if not confirm("是否加载已有方案？", default=False):
        return False

    # List available schemes
    names = sorted(schemes.keys())
    options = [f"{name} — {desc}" if desc else name for name, desc in
               ((n, schemes[n]) for n in names)]
    options.append("取消 (手动配置)")

    choice = select("选择方案", options, default=len(options))
    if choice == len(options):
        return False

    scheme_name = names[choice - 1]
    try:
        factors, weights = load_scheme(scheme_name)
    except ValueError as e:
        console.print(f"  [red]{e}[/red]")
        return False

    cfg.enabled_factors = factors
    cfg.weights = dict(weights)

    # Fill missing weights from defaults
    for name in factors:
        score_col = _factor_to_score_col(name)
        if score_col not in cfg.weights:
            cfg.weights[score_col] = DEFAULT_WEIGHTS.get(score_col, 0.0)

    console.print(f"  [green]已加载方案: {scheme_name}[/green]")
    console.print(f"  [dim]因子: {', '.join(sorted(factors))}[/dim]")
    console.print(f"  [dim]权重: {', '.join(f'{k}={v:+.2f}' for k, v in sorted(weights.items()))}[/dim]")
    return True


def step_save_scheme(cfg: WizardConfig) -> None:
    """Ask whether to save current config as a scheme."""
    console.print("\n[bold cyan]保存方案[/]")
    console.print("  [dim]可将当前因子和权重配置保存为方案，方便下次使用[/dim]")

    if not confirm("是否保存当前配置为方案？", default=False):
        return

    name = input_value("方案名称 (英文，如 my_strategy)", default="").strip()
    if not name:
        console.print("  [yellow]方案名为空，跳过保存[/yellow]")
        return

    # Check for overwrite
    existing = list_schemes()
    if name in existing:
        if not confirm(f"  方案 '{name}' 已存在，是否覆盖？", default=False):
            console.print("  [yellow]跳过保存[/yellow]")
            return

    description = input_value("方案描述", default="").strip()

    save_scheme(
        name=name,
        description=description,
        factors=cfg.enabled_factors,
        weights=cfg.weights,
    )
    console.print(f"  [green]方案 '{name}' 已保存[/green]")


def step_factor_selection(cfg: WizardConfig) -> None:
    """Step 5: Select factors with category grouping."""
    factors = get_registered_factors()

    # Build options for multi_select
    options = []
    for name, cls in factors.items():
        options.append({
            "id": name,
            "label": f"[[{cls.category}]] {name} — {getattr(cls, 'description_cn', None) or cls.description}",
        })

    # Default: all enabled
    all_names = set(factors.keys())
    defaults = cfg.enabled_factors if cfg.enabled_factors else all_names

    console.print("\n[bold cyan]因子选择[/]")
    selected = multi_select(
        "选择启用的因子（输入编号切换，回车确认）",
        options,
        defaults=defaults,
    )
    cfg.enabled_factors = selected

    # Initialize weights for selected factors
    new_weights = {}
    for name in selected:
        score_col = _factor_to_score_col(name)
        if score_col in cfg.weights:
            new_weights[score_col] = cfg.weights[score_col]
        elif score_col in DEFAULT_WEIGHTS:
            new_weights[score_col] = DEFAULT_WEIGHTS[score_col]
        else:
            new_weights[score_col] = 0.0
    cfg.weights = new_weights

    console.print(f"  [green]已选择 {len(selected)} 个因子[/green]")
    # Show selection summary
    disabled = all_names - selected
    if disabled:
        console.print(f"  [dim]已禁用: {', '.join(sorted(disabled))}[/dim]")
    else:
        console.print("  [dim]全部因子已启用[/dim]")


def step_weight_config(cfg: WizardConfig) -> None:
    """Step 6: Configure factor weights."""
    from src.factors.scorer import _factor_to_score_col

    factors = get_registered_factors()
    console.print("\n[bold cyan]因子权重配置[/]")
    console.print("  [dim]直接回车保持当前值，输入 'n' 归一化权重[/dim]")

    show_factor_table(factors, cfg.enabled_factors, cfg.weights)

    if not confirm("是否调整因子权重？", default=False):
        return

    while True:
        console.print("\n  [dim]输入因子编号设置权重，或输入 'n' 归一化，'d' 重置默认，回车完成[/dim]")
        enabled_list = sorted(cfg.enabled_factors)
        for i, name in enumerate(enabled_list, 1):
            score_col = _factor_to_score_col(name)
            desc = getattr(factors[name], "description_cn", None) or factors[name].description
            console.print(f"    {i}. {name} ({desc}) — 当前权重: {cfg.weights.get(score_col, 0):+.2f}")

        raw = input("操作: ").strip().lower()

        if not raw:
            break
        elif raw == "n":
            # Normalize weights: scale so sum of absolute values = 1
            total = sum(abs(v) for v in cfg.weights.values())
            if total > 0:
                cfg.weights = {k: v / total for k, v in cfg.weights.items()}
                console.print("  [green]权重已归一化[/green]")
                show_factor_table(factors, cfg.enabled_factors, cfg.weights)
            continue
        elif raw == "d":
            # Reset to defaults
            for name in cfg.enabled_factors:
                score_col = _factor_to_score_col(name)
                cfg.weights[score_col] = DEFAULT_WEIGHTS.get(score_col, 0.0)
            console.print("  [green]已重置为默认权重[/green]")
            show_factor_table(factors, cfg.enabled_factors, cfg.weights)
            continue

        # Try to parse as factor number
        try:
            idx = int(raw)
            if 1 <= idx <= len(enabled_list):
                name = enabled_list[idx - 1]
                score_col = _factor_to_score_col(name)
                current = cfg.weights.get(score_col, 0)
                new_val = input_value(f"  {name} 权重", default=f"{current:.2f}", value_type=float)
                cfg.weights[score_col] = float(new_val)
                console.print(f"  [green]{name}: {float(new_val):+.2f}[/green]")
            else:
                console.print("[red]编号超出范围[/red]")
        except (ValueError, IndexError):
            console.print("[red]无效输入[/red]")


def step_scoring(cfg: WizardConfig) -> None:
    """Step 7: Whether to compute scoring."""
    cfg.do_scoring = confirm("\n是否执行因子评分计算？", default=True)
    if not cfg.do_scoring:
        console.print("  [yellow]跳过评分计算[/yellow]")


def step_top20_display(
    df: pd.DataFrame,
    code2name: dict[str, str],
    cfg: WizardConfig,
) -> pd.DataFrame:
    """Step 8: Display top 20 stocks.

    Returns the top picks DataFrame.
    """
    from src.factors.scorer import select_top_n

    console.print("\n[bold cyan]选股结果[/]")
    latest_date = df["trade_date"].max()
    top_picks = select_top_n(df, latest_date, n=cfg.top_n)

    show_top_stocks(top_picks, code2name, n=cfg.top_n)
    return top_picks


def step_backtest_params(cfg: WizardConfig) -> None:
    """Step 9: Configure backtest parameters."""
    console.print("\n[bold cyan]回测参数配置[/]")

    cfg.initial_capital = float(input_value(
        "初始资金", default=str(int(cfg.initial_capital)), value_type=float,
    ))
    cfg.top_n = int(input_value(
        "持仓数量", default=str(cfg.top_n), value_type=int,
    ))

    freq_options = ["M (月频)", "W (周频)", "Q (季频)"]
    freq_map = {"M": 1, "W": 2, "Q": 3}
    default_freq = freq_map.get(cfg.rebalance_freq, 1)
    freq_choice = select("调仓频率", freq_options, default=default_freq)
    cfg.rebalance_freq = ["M", "W", "Q"][freq_choice - 1]

    # Risk control
    console.print("  [bold]风控参数[/bold]")
    console.print(f"    个股止损: {cfg.stop_loss:.2%}  个股止盈: {cfg.take_profit:.2%}  "
                  f"组合回撤止损: {cfg.max_drawdown_stop:.2%}  冷冻天数: {cfg.cooldown_days}")
    cfg.risk_control_enabled = confirm("是否启用风控？", default=cfg.risk_control_enabled)
    if cfg.risk_control_enabled:
        if confirm("  是否调整风控参数？", default=False):
            cfg.stop_loss = float(input_value(
                "个股止损阈值", default=f"{cfg.stop_loss:.2f}", value_type=float,
            ))
            cfg.take_profit = float(input_value(
                "个股止盈阈值", default=f"{cfg.take_profit:.2f}", value_type=float,
            ))
            cfg.max_drawdown_stop = float(input_value(
                "组合回撤止损", default=f"{cfg.max_drawdown_stop:.2f}", value_type=float,
            ))
            cfg.cooldown_days = int(input_value(
                "止损后冷冻天数", default=str(cfg.cooldown_days), value_type=int,
            ))

    # Position sizing
    ps_options = ["等权 (equal_weight)", "评分加权 (score_weighted)", "风险平价 (risk_parity)"]
    ps_map = {"equal_weight": 1, "score_weighted": 2, "risk_parity": 3}
    default_ps = ps_map.get(cfg.position_sizing_method, 1)
    ps_choice = select("仓位管理方式", ps_options, default=default_ps)
    cfg.position_sizing_method = ["equal_weight", "score_weighted", "risk_parity"][ps_choice - 1]

    # Backtest date range
    console.print("  [bold]回测日期区间[/bold]")
    console.print(f"    数据范围: {cfg.start_date} ~ {cfg.end_date}")
    bt_start = input_value("回测起始日期 (直接回车使用数据全量)", default=cfg.backtest_start or "")
    bt_end = input_value("回测结束日期 (直接回车使用数据全量)", default=cfg.backtest_end or "")
    if bt_start:
        cfg.backtest_start = bt_start
    if bt_end:
        cfg.backtest_end = bt_end


def get_param_summary(cfg: WizardConfig) -> dict:
    """Build a summary dict of wizard config for display."""
    from src.factors.scorer import _factor_to_score_col

    factors = get_registered_factors()
    factor_desc = ", ".join(
        f"{name}" for name in sorted(cfg.enabled_factors)
    )
    weight_desc = ", ".join(
        f"{_factor_to_score_col(name)}={cfg.weights.get(_factor_to_score_col(name), 0):+.2f}"
        for name in sorted(cfg.enabled_factors)
    )
    return {
        "股票范围": f"{INDEX_OPTIONS.get(cfg.index_code, cfg.index_code)} ({cfg.index_code})",
        "时间范围": f"{cfg.start_date} ~ {cfg.end_date}",
        "数据源": cfg.data_source,
        "刷新数据": "是" if cfg.refresh_data else "否",
        "启用因子": factor_desc,
        "因子权重": weight_desc,
        "初始资金": f"{cfg.initial_capital:,.0f}",
        "持仓数量": str(cfg.top_n),
        "调仓频率": cfg.rebalance_freq,
        "风控": f"启用 (止损={cfg.stop_loss}, 止盈={cfg.take_profit})" if cfg.risk_control_enabled else "未启用",
        "仓位管理": cfg.position_sizing_method,
    }


def run_pipeline(cfg: WizardConfig, output_dir: Path | None = None) -> None:
    """Execute the full pipeline with wizard config.

    Args:
        cfg: Wizard configuration.
        output_dir: Output directory for results. If None, uses DATA_DIR.
    """
    from src.config import TUSHARE_TOKEN
    from src.data.cleaner import clean_pipeline
    from src.data.fetcher import (
        fetch_daily_basic,
        get_index_constituents,
        sync_stocks_data,
    )
    from src.data.industry import get_industry_map
    from src.data.storage import (
        load_daily_basic,
        load_daily_price,
        merge_fundamentals,
        save_daily_basic,
        save_daily_price,
    )
    from src.factors.scorer import compute_total_score, select_top_n, standardize_factors
    from src.factors.ic_analyzer import compute_future_return, evaluate_factor
    from src.factors.adaptive_weights import compute_adaptive_weights
    from src.factors.industry_neutral import apply_industry_constraint
    from src.backtest import BacktestEngine
    from src.visualization.charts import generate_all_charts
    from src.visualization.backtest_charts import generate_backtest_charts

    # Import factors to trigger registration
    import src.factors.momentum
    import src.factors.volume
    import src.factors.volatility
    import src.factors.rsi
    import src.factors.ma_deviation
    import src.factors.turnover
    import src.factors.intraday_range
    import src.factors.valuation
    import src.factors.return_20d
    import src.factors.trend_60d

    out = output_dir or DATA_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Override data source if needed
    if cfg.data_source == "tushare" and TUSHARE_TOKEN:
        import src.data.tushare_fetcher as tf
        _get_index_constituents = tf.get_index_constituents
        _sync_stocks_data = tf.sync_stocks_data
        _fetch_daily_basic = tf.fetch_daily_basic

        def _get_index_daily(symbol: str = "000300", start_date: str = "", end_date: str = "", **kwargs):
            ts_code = symbol if "." in symbol else f"{symbol}.SH"
            return tf.get_index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date, **kwargs)
    else:
        import src.data.akshare_fetcher as af
        _get_index_constituents = af.get_index_constituents
        _sync_stocks_data = af.sync_stocks_data
        _fetch_daily_basic = af.fetch_daily_basic
        _get_index_daily = af.get_index_daily

    # Step 1: Get stock list
    console.print("\n[bold green]▶ 获取股票列表...[/]")
    if cfg.index_code == "all":
        # For all A-shares, we'd need a different approach
        console.print("[yellow]全部A股模式: 使用沪深300作为替代[/yellow]")
        constituents = _get_index_constituents("000300")
    else:
        constituents = _get_index_constituents(cfg.index_code)
    ts_codes = constituents["ts_code"].tolist()
    code2name = dict(zip(constituents["ts_code"], constituents["name"]))
    console.print(f"  共 {len(ts_codes)} 只股票")

    # Step 2: Data sync
    console.print("\n[bold green]▶ 同步数据...[/]")
    if cfg.refresh_data:
        new_data = _sync_stocks_data(ts_codes, end_date=cfg.end_date)
        if new_data.empty:
            console.print("  数据已是最新，使用缓存")
            df = load_daily_price(start_date=cfg.start_date, end_date=cfg.end_date)
        else:
            console.print(f"  获取 {len(new_data)} 行新数据")
            df = load_daily_price(start_date=cfg.start_date, end_date=cfg.end_date)
    else:
        df = load_daily_price(start_date=cfg.start_date, end_date=cfg.end_date)

    if df.empty:
        console.print("[red]错误：未获取到数据！[/red]")
        return

    # Step 3: Clean
    console.print("\n[bold green]▶ 清洗数据...[/]")
    df, report = clean_pipeline(df)
    console.print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只股票")
    save_daily_price(df)

    # Step 4: Fundamentals
    console.print("\n[bold green]▶ 获取基本面数据...[/]")
    basic_df = load_daily_basic(start_date=cfg.start_date, end_date=cfg.end_date)
    if basic_df.empty:
        basic_df = _fetch_daily_basic(ts_codes, start_date=cfg.start_date, end_date=cfg.end_date)
        if not basic_df.empty:
            save_daily_basic(basic_df)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    # Step 5: Calculate factors (only enabled ones)
    console.print("\n[bold green]▶ 计算因子...[/]")
    factors = get_registered_factors()
    factor_cols = []
    for name in sorted(cfg.enabled_factors):
        if name in factors:
            factor = factors[name]()
            df = factor.calculate(df)
            factor_cols.append(factor.factor_name)
            desc = getattr(factor, "description_cn", None) or factor.description
            console.print(f"  ✓ {factor.factor_name} ({desc})")

    # Step 6: Scoring
    if cfg.do_scoring:
        console.print("\n[bold green]▶ 标准化 & 综合打分...[/]")
        df = standardize_factors(df, factor_cols)
        df = compute_total_score(df, weights=cfg.weights)

        # Show top picks
        latest_date = df["trade_date"].max()
        top_picks = select_top_n(df, latest_date, n=cfg.top_n)

        # Industry neutral
        if cfg.industry_neutral_enabled:
            industry_map = get_industry_map(ts_codes)
            if industry_map:
                top_picks = apply_industry_constraint(
                    top_picks, industry_map,
                    max_pct=cfg.max_industry_pct, n=cfg.top_n,
                )

        show_top_stocks(top_picks, code2name, n=cfg.top_n)

        # IC analysis
        console.print("\n[bold green]▶ IC 分析...[/]")
        df = compute_future_return(df, n_days=20)
        return_col = "future_return_20d"
        ic_results = {}
        for name in sorted(cfg.enabled_factors):
            if name not in factors:
                continue
            factor_col = factors[name]().factor_name
            if factor_col not in df.columns:
                continue
            result = evaluate_factor(df, factor_col, return_col)
            ic_results[factor_col] = result
            s = result["summary"]
            desc = getattr(factors[name], "description_cn", None) or factors[name].description
            console.print(
                f"  {factor_col:20s}  {desc}  IC均值: {s['ic_mean']:+.4f}  "
                f"ICIR: {s['icir']:+.4f}  胜率: {s['win_rate']:.1%}  "
                f"→ {result['verdict']}"
            )
    else:
        top_picks = pd.DataFrame()
        ic_results = {}

    # Step 7: Backtest
    if cfg.do_scoring and (cfg.auto_confirm or confirm("\n是否运行回测？", default=True)):
        console.print("\n[bold green]▶ 运行回测...[/]")

        # Filter by backtest date range (after factor calculation for warmup)
        bt_df = df
        if cfg.backtest_start or cfg.backtest_end:
            bt_df = df.copy()
            if cfg.backtest_start:
                bt_df = bt_df[bt_df["trade_date"] >= cfg.backtest_start]
            if cfg.backtest_end:
                bt_df = bt_df[bt_df["trade_date"] <= cfg.backtest_end]
            bt_dates = bt_df["trade_date"].unique()
            if len(bt_dates) == 0:
                data_range = f"{df['trade_date'].min()} ~ {df['trade_date'].max()}"
                console.print(f"[red]回测区间 {cfg.backtest_start or ''} ~ {cfg.backtest_end or ''} 无数据[/red]")
                console.print(f"[yellow]数据实际范围: {data_range}[/yellow]")
                return
            if "total_score" in bt_df.columns:
                valid_scores = bt_df["total_score"].notna().sum()
                if valid_scores == 0:
                    console.print(f"[red]回测区间内无有效评分数据（基本面数据可能缺失）[/red]")
                    console.print(f"[yellow]建议扩大数据范围或使用不依赖基本面的因子[/yellow]")
                    return
            console.print(f"  回测区间: {bt_dates.min()} ~ {bt_dates.max()} ({len(bt_dates)} 个交易日)")

        bt_industry_map = None
        if cfg.industry_neutral_enabled:
            bt_industry_map = get_industry_map(ts_codes)

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
            min_weight=cfg.min_weight,
            max_weight=cfg.max_weight,
            industry_map=bt_industry_map,
            max_industry_pct=cfg.max_industry_pct,
        )

        benchmark_df = _get_index_daily(
            symbol=cfg.index_code if cfg.index_code != "all" else "000300",
            start_date=cfg.backtest_start or cfg.start_date,
            end_date=cfg.backtest_end or cfg.end_date,
        )

        bt_result = engine.run(bt_df, benchmark_df=benchmark_df)
        show_backtest_summary(bt_result)

        # Create result directory early so charts go there too
        from datetime import datetime as dt
        result_dir = out / "results" / dt.now().strftime("%Y%m%d_%H%M%S")
        result_dir.mkdir(parents=True, exist_ok=True)

        # Generate charts into result directory
        console.print("\n[bold green]▶ 生成图表...[/]")
        generate_all_charts(df, ic_results, factor_cols, code2name, output_dir=result_dir)
        generate_backtest_charts(bt_result, output_dir=result_dir)
        console.print(f"  图表已生成到 {result_dir}")

        # Save results into same directory
        _save_results(cfg, bt_result, top_picks, ic_results, code2name, result_dir)
    else:
        # Save partial results (no backtest)
        from datetime import datetime as dt
        result_dir = out / "results" / dt.now().strftime("%Y%m%d_%H%M%S")
        result_dir.mkdir(parents=True, exist_ok=True)
        _save_results(cfg, None, top_picks, ic_results, code2name, result_dir)

    console.print("\n[bold green]✓ 完成！[/bold green]")


def _save_results(
    cfg: WizardConfig,
    bt_result,
    top_picks: pd.DataFrame,
    ic_results: dict,
    code2name: dict[str, str],
    result_dir: Path,
) -> None:
    """Save all results to result directory with summary markdown.

    Args:
        cfg: Wizard config.
        bt_result: BacktestResult or None.
        top_picks: Top stocks DataFrame.
        ic_results: IC analysis results.
        code2name: Stock code to name mapping.
        result_dir: Result directory (already created by caller).
    """
    from datetime import datetime as dt

    result_dir.mkdir(parents=True, exist_ok=True)

    # Build summary markdown
    lines = [
        f"# QTrade 回测报告",
        f"",
        f"**生成时间**: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"## 参数配置",
        f"",
    ]

    summary = get_param_summary(cfg)
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")

    # Top picks
    if not top_picks.empty:
        lines.append(f"\n## Top {len(top_picks)} 股票\n")
        lines.append("| 排名 | 编码 | 名称 | 综合得分 |")
        lines.append("|------|------|------|----------|")
        for i, (_, row) in enumerate(top_picks.iterrows(), 1):
            ts_code = row["ts_code"]
            name = code2name.get(ts_code, "—")
            score = f"{row['total_score']:.2f}"
            lines.append(f"| {i} | {ts_code} | {name} | {score} |")

    # IC results
    if ic_results:
        lines.append(f"\n## IC 分析\n")
        lines.append("| 因子 | IC均值 | ICIR | 胜率 | 判定 |")
        lines.append("|------|--------|------|------|------|")
        for factor_col, result in ic_results.items():
            s = result["summary"]
            lines.append(
                f"| {factor_col} | {s['ic_mean']:+.4f} | {s['icir']:+.4f} | "
                f"{s['win_rate']:.1%} | {result['verdict']} |"
            )

    # Backtest results
    if bt_result is not None:
        metrics = bt_result.metrics
        lines.append(f"\n## 回测结果\n")
        lines.append(f"- **年化收益率**: {metrics.get('annual_return', 0):.2%}")
        lines.append(f"- **最大回撤**: {metrics.get('max_drawdown', 0):.2%}")
        lines.append(f"- **夏普比率**: {metrics.get('sharpe_ratio', 0):.2f}")
        lines.append(f"- **总交易笔数**: {metrics.get('trade_count', metrics.get('total_trades', 0))}")
        lines.append(f"- **胜率**: {metrics.get('win_rate', 0):.1%}")
        lines.append(f"- **期末净值**: {metrics.get('final_nav', 0):.4f}")

        # Save trades
        if not bt_result.trades.empty:
            trades_path = result_dir / "backtest_trades.csv"
            bt_result.trades.to_csv(trades_path, index=False)

        # Save NAV
        if not bt_result.nav_series.empty:
            nav_path = result_dir / "backtest_nav.csv"
            bt_result.nav_series.to_csv(nav_path, index=False)

    # Write markdown
    report_path = result_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    console.print(f"\n  [green]结果已保存到: {result_dir}[/green]")
    console.print(f"  [green]报告: {report_path}[/green]")


def run_wizard(cli_args: dict[str, Any] | None = None) -> None:
    """Run the interactive wizard, optionally pre-filled with CLI args.

    Args:
        cli_args: Dict of pre-filled values from argparse. Missing values
                  will be collected interactively.
    """
    cli_args = cli_args or {}
    cfg = WizardConfig()

    show_banner()

    # Pre-fill from CLI args
    if "index" in cli_args:
        cfg.index_code = cli_args["index"]
    if "start" in cli_args:
        cfg.start_date = cli_args["start"]
    if "end" in cli_args:
        cfg.end_date = cli_args["end"]
    if "source" in cli_args:
        cfg.data_source = cli_args["source"]
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
    if cli_args.get("factors"):
        cfg.enabled_factors = set(cli_args["factors"].split(","))

    # Risk control params
    risk_keys = ["stop_loss", "take_profit", "max_drawdown", "cooldown_days"]
    has_risk_arg = any(cli_args.get(k) is not None for k in risk_keys)
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
    if cli_args.get("industry_neutral"):
        cfg.industry_neutral_enabled = True
    if "max_industry_pct" in cli_args:
        cfg.max_industry_pct = cli_args["max_industry_pct"]

    # Backtest date range
    if "backtest_start" in cli_args:
        cfg.backtest_start = cli_args["backtest_start"]
    if "backtest_end" in cli_args:
        cfg.backtest_end = cli_args["backtest_end"]

    # Pre-load scheme from CLI --scheme arg
    scheme_loaded_from_cli = False
    if cli_args.get("scheme"):
        from src.scheme import ensure_schemes_file
        ensure_schemes_file()
        try:
            factors, weights = load_scheme(cli_args["scheme"])
            cfg.enabled_factors = factors
            cfg.weights = dict(weights)
            for name in factors:
                score_col = _factor_to_score_col(name)
                if score_col not in cfg.weights:
                    cfg.weights[score_col] = DEFAULT_WEIGHTS.get(score_col, 0.0)
            scheme_loaded_from_cli = True
        except ValueError as e:
            console.print(f"  [red]{e}[/red]")

    # Interactive steps (skip if CLI arg provided)
    console.print("\n[bold]═══ 第 1 步: 股票范围 ═══[/bold]")
    if "index" not in cli_args:
        step_index(cfg)
    else:
        console.print(f"  [green]已指定: {INDEX_OPTIONS.get(cfg.index_code, cfg.index_code)}[/green]")

    console.print("\n[bold]═══ 第 2 步: 时间范围 ═══[/bold]")
    if "start" not in cli_args and "end" not in cli_args:
        step_date_range(cfg)
    else:
        console.print(f"  [green]{cfg.start_date} ~ {cfg.end_date}[/green]")

    console.print("\n[bold]═══ 第 3 步: 数据刷新 ═══[/bold]")
    step_data_refresh(cfg)

    console.print("\n[bold]═══ 第 4 步: 数据源 ═══[/bold]")
    if "source" not in cli_args:
        step_data_source(cfg)
    else:
        console.print(f"  [green]已指定: {cfg.data_source}[/green]")

    # Step 5: Scheme selection (skip if loaded from CLI or --factors provided)
    scheme_loaded = scheme_loaded_from_cli
    if not scheme_loaded and not cli_args.get("factors"):
        console.print("\n[bold]═══ 第 5 步: 方案选择 ═══[/bold]")
        scheme_loaded = step_scheme_selection(cfg)

    # Step 6: Factor selection (skip if scheme loaded and user doesn't want to adjust)
    skip_factor_weight = False
    if scheme_loaded:
        console.print("\n[bold]═══ 第 6 步: 因子 & 权重 ═══[/bold]")
        console.print("  [dim]已从方案加载因子和权重[/dim]")
        skip_factor_weight = not confirm("是否调整因子和权重？", default=False)

    if not skip_factor_weight:
        console.print("\n[bold]═══ 第 6 步: 因子选择 ═══[/bold]")
        step_factor_selection(cfg)

        console.print("\n[bold]═══ 第 7 步: 权重配置 ═══[/bold]")
        step_weight_config(cfg)

        # Offer to save as scheme after configuring factors/weights
        step_save_scheme(cfg)
    else:
        # Show loaded config summary
        factors = get_registered_factors()
        show_factor_table(factors, cfg.enabled_factors, cfg.weights)

    console.print("\n[bold]═══ 第 8 步: 评分计算 ═══[/bold]")
    step_scoring(cfg)

    console.print("\n[bold]═══ 第 9 步: 回测参数 ═══[/bold]")
    step_backtest_params(cfg)

    # Show parameter summary before execution
    show_param_summary(get_param_summary(cfg))

    if not confirm("\n确认运行？", default=True):
        console.print("[yellow]已取消[/yellow]")
        return

    run_pipeline(cfg)
