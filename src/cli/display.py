"""Rich-formatted display utilities for the CLI wizard."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()


def show_banner():
    """Display the QTrade banner."""
    console.print(Panel(
        "[bold cyan]QTrade 量化交易系统 — 交互式向导[/bold cyan]\n"
        "[dim]按 Enter 使用默认值，逐步完成选股到回测的完整流程[/dim]",
        border_style="cyan",
    ))


def show_factor_table(factors: dict, enabled: set[str], weights: dict[str, float]):
    """Display factor selection table grouped by category.

    Args:
        factors: Dict from get_registered_factors() {name: cls}.
        enabled: Set of enabled factor names.
        weights: Dict mapping score column name to weight.
    """
    from src.factors.scorer import _factor_to_score_col

    # Assign sequential numbers matching get_registered_factors() order
    factor_list_all = list(factors.items())

    # Group by category
    categories: dict[str, list] = {}
    for idx, (name, cls) in enumerate(factor_list_all, 1):
        cat = cls.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((idx, name, cls))

    for cat_name, factor_list in categories.items():
        console.print(f"\n[bold yellow]▎{cat_name}[/bold yellow]")

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("编号", width=4, justify="right")
        table.add_column("状态", width=6)
        table.add_column("因子名", style="cyan", width=25)
        table.add_column("描述", style="dim")
        table.add_column("权重", width=10, justify="right")

        for idx, factor_name, cls in factor_list:
            is_on = factor_name in enabled
            status = "[green]✓ 开[/green]" if is_on else "[dim]○ 关[/dim]"
            score_col = _factor_to_score_col(factor_name)
            weight_val = weights.get(score_col, 0)
            weight_str = f"{weight_val:+.2f}" if is_on else "[dim]—[/dim]"
            table.add_row(str(idx), status, factor_name, getattr(cls, "description_cn", None) or cls.description, weight_str)

        console.print(table)


def show_top_stocks(top_df, code2name: dict[str, str] | None = None, n: int = 20):
    """Display top N stocks as a Rich table.

    Args:
        top_df: DataFrame with ts_code, total_score columns.
        code2name: Optional mapping from ts_code to stock name.
        n: Number of stocks to show.
    """
    df = top_df.head(n)

    table = Table(title=f"Top {len(df)} 股票", show_header=True, header_style="bold magenta")
    table.add_column("排名", style="bold", width=6, justify="right")
    table.add_column("股票编码", style="cyan", width=14)
    table.add_column("股票名称", style="green", width=14)
    table.add_column("综合得分", justify="right", width=10)

    for i, (_, row) in enumerate(df.iterrows(), 1):
        ts_code = row["ts_code"]
        name = code2name.get(ts_code, "—") if code2name else "—"
        score = f"{row['total_score']:.2f}"
        table.add_row(str(i), ts_code, name, score)

    console.print(table)


def show_param_summary(params: dict):
    """Display a summary panel of all configured parameters.

    Args:
        params: Dict of parameter name → value.
    """
    lines = []
    for key, value in params.items():
        lines.append(f"  [cyan]{key}[/]: {value}")
    console.print(Panel(
        "\n".join(lines),
        title="[bold]参数确认[/bold]",
        border_style="green",
    ))


def show_backtest_summary(bt_result):
    """Display backtest result summary.

    Args:
        bt_result: BacktestResult object with metrics.
    """
    metrics = bt_result.metrics

    table = Table(title="回测结果", show_header=True, header_style="bold green")
    table.add_column("指标", style="cyan", width=20)
    table.add_column("值", justify="right", width=16)

    rows = [
        ("年化收益率", f"{metrics.get('annual_return', 0):.2%}"),
        ("最大回撤", f"{metrics.get('max_drawdown', 0):.2%}"),
        ("夏普比率", f"{metrics.get('sharpe_ratio', 0):.2f}"),
        ("总交易笔数", f"{metrics.get('trade_count', metrics.get('total_trades', 0))}"),
        ("胜率", f"{metrics.get('win_rate', 0):.1%}"),
        ("期末净值", f"{metrics.get('final_nav', 0):.4f}"),
    ]

    for label, value in rows:
        table.add_row(label, value)

    console.print(table)


def show_data_refresh_info(latest_date: str | None, stock_count: int):
    """Display data cache status.

    Args:
        latest_date: Latest data date in DB, or None if no data.
        stock_count: Number of stocks in DB.
    """
    if latest_date:
        console.print(f"  [green]缓存数据:[/green] 最近更新日期 [bold]{latest_date}[/bold], 共 {stock_count} 只股票")
    else:
        console.print("  [yellow]无缓存数据，将首次获取[/yellow]")
