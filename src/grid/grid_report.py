"""Grid trading report and chart generation."""

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from src.grid.grid_params import GridParams
from src.grid.grid_result import GridBacktestResult

matplotlib.use("Agg")
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _param_section(params: GridParams) -> list[str]:
    """Generate markdown lines for grid parameter display."""
    levels = params.get_grid_levels()
    return [
        "### 网格参数",
        "",
        f"| 参数 | 值 |",
        f"|------|----|",
        f"| 价格下限 | {params.price_lower:.3f} |",
        f"| 价格上限 | {params.price_upper:.3f} |",
        f"| 网格层数 | {params.grid_levels} |",
        f"| 间距模式 | {'等间距' if params.grid_mode == 'equal' else '等比间距'} |",
        f"| 每层交易股数 | {params.order_shares} |",
        f"| 初始底仓 | {params.base_shares} 股 |",
        f"| 买入佣金 | {params.buy_commission:.4%} |",
        f"| 卖出佣金 | {params.sell_commission:.4%} |",
        f"| 印花税(卖) | {params.stamp_tax:.4%} |",
        f"| 网格层价格 | {', '.join(f'{p:.3f}' for p in levels)} |",
        "",
    ]


def generate_report(
    results: dict[str, GridBacktestResult],
    params_map: dict[str, GridParams],
    start_date: str,
    end_date: str,
    output_dir: Path,
) -> Path:
    """Generate full markdown report and charts for grid backtest.

    Args:
        results: Dict mapping ts_code to GridBacktestResult.
        params_map: Dict mapping ts_code to GridParams used.
        start_date: Backtest start date.
        end_date: Backtest end date.
        output_dir: Directory to write report and charts.

    Returns:
        Path to the generated report.md.
    """
    from datetime import datetime as dt

    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 网格交易回测报告",
        f"**生成时间**: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**回测区间**: {start_date} ~ {end_date}",
        f"**股票数量**: {len(results)}",
        "",
        "## 回测汇总",
        "",
        "| 股票 | 年化收益 | 最大回撤 | 夏普 | 成交笔数 | 网格收益 | 底仓收益 | 总收益 |",
        "|------|---------|---------|------|---------|---------|---------|--------|",
    ]

    for ts_code, r in results.items():
        m = r.metrics
        a = r.attribution
        lines.append(
            f"| {ts_code} | {m.get('annual_return', 0):.2%} | "
            f"{m.get('max_drawdown', 0):.2%} | {m.get('sharpe_ratio', 0):.2f} | "
            f"{m.get('trade_count', 0)} | "
            f"{a.get('grid_trading_return', 0):.2%} | "
            f"{a.get('base_position_return', 0):.2%} | "
            f"{a.get('total_return', 0):.2%} |"
        )

    # Per-stock detail
    for ts_code, r in results.items():
        lines.append(f"\n## {ts_code} 详细结果")

        # Parameters
        if ts_code in params_map:
            lines.extend(_param_section(params_map[ts_code]))

        # Grid level stats
        lines.append(f"### 网格层统计")
        if not r.grid_level_stats.empty:
            lines.append(
                "| 层级 | 价格 | 买入次数 | 卖出次数 | 买入金额 | 卖出金额 | 层收益 |"
            )
            lines.append(
                "|------|------|---------|---------|---------|---------|--------|"
            )
            for _, row in r.grid_level_stats.iterrows():
                bc = int(row['buy_count']) if pd.notna(row.get('buy_count')) else 0
                sc = int(row['sell_count']) if pd.notna(row.get('sell_count')) else 0
                ba = row.get('total_buy_amount', 0) or 0
                sa = row.get('total_sell_amount', 0) or 0
                gp = row.get('grid_profit', 0) or 0
                lines.append(
                    f"| {int(row['grid_level'])} | {row['grid_price']:.3f} | "
                    f"{bc} | {sc} | "
                    f"{ba:.0f} | {sa:.0f} | "
                    f"{gp:.2f} |"
                )

        # Attribution
        lines.append(f"\n### 收益归因")
        a = r.attribution
        lines.append(f"- 网格交易收益: {a.get('grid_trading_return', 0):.2%}")
        lines.append(f"- 底仓持仓收益: {a.get('base_position_return', 0):.2%}")
        lines.append(f"- 总收益: {a.get('total_return', 0):.2%}")
        lines.append(f"- 手续费合计: {a.get('total_commission', 0):.2f}")
        lines.append(f"- 印花税合计: {a.get('total_stamp_tax', 0):.2f}")

        # All trades
        if not r.trades.empty:
            n_total = len(r.trades)
            lines.append(f"\n### 成交明细（共 {n_total} 笔）")
            lines.append(
                "| # | 时间 | 方向 | 价格 | 股数 | 金额 | 手续费 | 层级 |"
            )
            lines.append(
                "|------|------|------|------|------|------|------|------|"
            )
            for i, (_, t) in enumerate(r.trades.iterrows(), 1):
                level_val = t.get('level', -1)
                level_str = str(int(level_val)) if pd.notna(level_val) else '-'
                lines.append(
                    f"| {i} | {t.get('time', '')} | {t.get('action', '')} | "
                    f"{t.get('price', 0):.3f} | {int(t.get('shares', 0))} | "
                    f"{t.get('amount', 0):.0f} | {t.get('commission', 0):.2f} | "
                    f"{level_str} |"
                )

            # Save trades CSV
            trades_path = output_dir / f"{ts_code}_trades.csv"
            r.trades.to_csv(trades_path, index=False)

        # Save NAV CSV
        if not r.nav_series.empty:
            nav_path = output_dir / f"{ts_code}_nav.csv"
            r.nav_series.to_csv(nav_path, index=False)

        # Charts
        if ts_code in params_map:
            chart_paths = _generate_charts(
                ts_code, r, params_map[ts_code], output_dir,
            )
            lines.append(f"\n### 图表")
            for name, path in chart_paths.items():
                lines.append(f"- [{name}]({Path(path).name})")

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _generate_charts(
    ts_code: str,
    result: GridBacktestResult,
    params: GridParams,
    output_dir: Path,
) -> dict[str, str]:
    """Generate grid backtest charts. Returns {name: filepath}."""
    charts = {}

    # 1. Price + grid overlay + trade markers
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1, 1]})
    ax1, ax2, ax3 = axes

    # Price line from NAV data
    if not result.nav_series.empty and "nav" in result.nav_series.columns:
        nav = result.nav_series
        ax1.plot(range(len(nav)), nav["nav"], linewidth=0.8, color="#1a1a2e", label="NAV")
        ax1.set_ylabel("NAV (元)")
        ax1.legend(loc="upper left")
        ax1.grid(alpha=0.3)

    # Grid level lines
    levels = params.get_grid_levels()
    mid_nav = result.nav_series["nav"].mean() if not result.nav_series.empty else 100000
    for i, gp in enumerate(levels):
        ls = "--" if i in (0, len(levels) - 1) else ":"
        alpha = 0.8 if i in (0, len(levels) - 1) else 0.3
        ax1.axhline(y=mid_nav * (gp / levels[len(levels)//2]),
                    color="orange", linestyle=ls, alpha=alpha, linewidth=0.5)

    # Trade markers — use row label (pandas .name attribute) as index;
    # map proportionally across nav series length for visual spread
    if not result.trades.empty:
        nav_len = max(len(result.nav_series), 1)
        trade_len = max(len(result.trades), 1)
        for i, (_, t) in enumerate(result.trades.iterrows()):
            idx = min(int(i * nav_len / trade_len), nav_len - 1)
            color = "#e74c3c" if str(t.get("action", "")) == "BUY" else "#2ecc71"
            marker = "^" if str(t.get("action", "")) == "BUY" else "v"
            ax1.scatter(idx, result.nav_series["nav"].iloc[min(int(idx), len(result.nav_series)-1)],
                       c=color, marker=marker, s=20, alpha=0.6)

    # Drawdown
    if not result.nav_series.empty and "nav" in result.nav_series.columns:
        peak = result.nav_series["nav"].cummax()
        dd = (result.nav_series["nav"] - peak) / peak * 100
        ax2.fill_between(range(len(dd)), 0, dd, color="#e74c3c", alpha=0.3)
        ax2.set_ylabel("回撤 (%)")
        ax2.grid(alpha=0.3)

    # Trade count per period (daily aggregation)
    if not result.trades.empty:
        times = pd.to_datetime(result.trades["time"], format="%Y%m%d%H%M%S", errors="coerce")
        daily = times.dt.strftime("%Y%m%d").value_counts().sort_index()
        ax3.bar(range(len(daily)), daily.values, width=0.8, color="#3498db", alpha=0.7)
        ax3.set_ylabel("日成交笔数")
        ax3.grid(alpha=0.3)

    ax3.set_xlabel("时间 →")
    fig.suptitle(f"{ts_code} 网格交易回测", fontsize=14, fontweight="bold")
    plt.tight_layout()
    chart_path = output_dir / f"{ts_code}_overview.png"
    fig.savefig(chart_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    charts["概览 (NAV+回撤+成交)"] = str(chart_path)

    # 2. Grid level profit bar chart
    if not result.grid_level_stats.empty:
        stats = result.grid_level_stats
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in stats["grid_profit"]]
        ax.barh([f"L{i} ({p:.2f})" for i, p in zip(stats["grid_level"], stats["grid_price"])],
                stats["grid_profit"], color=colors, alpha=0.8)
        ax.axvline(0, color="black", linewidth=0.5)
        ax.set_xlabel("层收益 (元)")
        ax.set_title(f"{ts_code} 网格层收益分布")
        plt.tight_layout()
        chart_path = output_dir / f"{ts_code}_level_profit.png"
        fig.savefig(chart_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        charts["网格层收益"] = str(chart_path)

    return charts
