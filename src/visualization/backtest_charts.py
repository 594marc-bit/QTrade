"""Backtest visualization: NAV curve, drawdown, performance summary, monthly returns heatmap."""

import platform
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from src.config import DATA_DIR

CHARTS_DIR = DATA_DIR / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _setup_chinese_font():
    """Configure matplotlib for Chinese text display."""
    system = platform.system()
    if system == "Darwin":
        matplotlib.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti TC", "Arial Unicode MS"]
    elif system == "Windows":
        matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
    else:
        matplotlib.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "SimHei"]
    matplotlib.rcParams["axes.unicode_minus"] = False


_setup_chinese_font()


def plot_nav_curve(nav_series: pd.DataFrame, save: bool = True) -> str | None:
    """Plot strategy NAV vs benchmark NAV.

    Args:
        nav_series: DataFrame with columns [date, nav, benchmark_nav, position_count].
        save: Whether to save to file.

    Returns:
        Path to saved chart, or None.
    """
    fig, ax = plt.subplots(figsize=(14, 6))

    dates = pd.to_datetime(nav_series["date"])
    ax.plot(dates, nav_series["nav"], label="策略净值", linewidth=1.5, color="#2196F3")

    if "benchmark_nav" in nav_series.columns and not nav_series["benchmark_nav"].isna().all():
        ax.plot(
            dates, nav_series["benchmark_nav"],
            label="沪深300", linewidth=1.2, color="#9E9E9E", linestyle="--",
        )

    ax.set_title("策略净值曲线", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=12)
    ax.set_ylabel("净值", fontsize=12)
    ax.legend(fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()

    path = CHARTS_DIR / "backtest_nav.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_drawdown(nav_series: pd.DataFrame, save: bool = True) -> str | None:
    """Plot drawdown area chart with max drawdown annotation.

    Args:
        nav_series: DataFrame with columns [date, nav].
        save: Whether to save to file.

    Returns:
        Path to saved chart, or None.
    """
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]
    peak = nav.cummax()
    drawdown = (nav - peak) / peak

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(drawdown.index, drawdown.values, 0, color="#FF5252", alpha=0.4)
    ax.plot(drawdown.index, drawdown.values, color="#FF5252", linewidth=0.8)

    # Annotate max drawdown
    min_idx = drawdown.idxmin()
    min_val = drawdown.min()
    ax.annotate(
        f"最大回撤: {min_val:.2%}",
        xy=(min_idx, min_val),
        xytext=(min_idx, min_val * 0.5),
        fontsize=11,
        color="#D32F2F",
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#D32F2F"),
    )

    ax.set_title("历史回撤", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=12)
    ax.set_ylabel("回撤", fontsize=12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    path = CHARTS_DIR / "backtest_drawdown.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_performance_summary(metrics: dict, save: bool = True) -> str | None:
    """Plot performance metrics summary table.

    Args:
        metrics: Dict of performance metrics.
        save: Whether to save to file.

    Returns:
        Path to saved chart, or None.
    """
    rows = [
        ("总收益率", f"{metrics.get('total_return', 0):.2%}"),
        ("年化收益率", f"{metrics.get('annual_return', 0):.2%}"),
        ("夏普比率", f"{metrics.get('sharpe_ratio', 0):.4f}"),
        ("最大回撤", f"{metrics.get('max_drawdown', 0):.2%}"),
        ("Calmar 比率", f"{metrics.get('calmar_ratio', 0):.4f}"),
        ("胜率", f"{metrics.get('win_rate', 0):.2%}"),
        ("调仓次数", f"{metrics.get('trade_count', 0)}"),
    ]
    if "alpha" in metrics:
        rows.append(("Alpha", f"{metrics['alpha']:.2%}"))
        rows.append(("信息比率", f"{metrics.get('information_ratio', 0):.4f}"))

    fig, ax = plt.subplots(figsize=(6, len(rows) * 0.6 + 1))
    ax.axis("off")

    table = ax.table(
        cellText=[[r[1]] for r in rows],
        rowLabels=[r[0] for r in rows],
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.5)

    ax.set_title("回测绩效摘要", fontsize=16, fontweight="bold", pad=20)

    path = CHARTS_DIR / "backtest_summary.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_monthly_returns(nav_series: pd.DataFrame, save: bool = True) -> str | None:
    """Plot monthly returns heatmap.

    Args:
        nav_series: DataFrame with columns [date, nav].
        save: Whether to save to file.

    Returns:
        Path to saved chart, or None.
    """
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]

    # Monthly returns
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 2:
        return None

    monthly_ret = monthly.pct_change().dropna()

    # Build year × month matrix
    df_ret = pd.DataFrame({
        "year": monthly_ret.index.year,
        "month": monthly_ret.index.month,
        "return": monthly_ret.values,
    })
    pivot = df_ret.pivot(index="year", columns="month", values="return")

    fig, ax = plt.subplots(figsize=(14, max(4, len(pivot) * 0.8)))

    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "rg", ["#FF5252", "#FFFFFF", "#4CAF50"]
    )
    im = ax.imshow(pivot.values, cmap=cmap, aspect="auto", vmin=-0.15, vmax=0.15)

    ax.set_xticks(range(12))
    ax.set_xticklabels([f"{m}月" for m in range(1, 13)])
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index)

    # Annotate cells
    for i in range(len(pivot)):
        for j in range(12):
            j_idx = j + 1  # months are 1-12
            if j_idx in pivot.columns:
                val = pivot.iloc[i].get(j_idx, np.nan)
                if not np.isnan(val):
                    color = "white" if abs(val) > 0.08 else "black"
                    ax.text(j, i, f"{val:.1%}", ha="center", va="center", fontsize=9, color=color)

    ax.set_title("月度收益率热力图", fontsize=16, fontweight="bold")
    fig.colorbar(im, ax=ax, label="月度收益率")

    path = CHARTS_DIR / "backtest_monthly_returns.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def generate_backtest_charts(result) -> list[str]:
    """Generate all backtest visualization charts.

    Args:
        result: BacktestResult object.

    Returns:
        List of saved chart paths.
    """
    paths = []

    if result.nav_series is not None and not result.nav_series.empty:
        p = plot_nav_curve(result.nav_series)
        if p:
            paths.append(p)
            print(f"  净值曲线: {p}")

        p = plot_drawdown(result.nav_series)
        if p:
            paths.append(p)
            print(f"  回撤图: {p}")

        p = plot_monthly_returns(result.nav_series)
        if p:
            paths.append(p)
            print(f"  月度收益: {p}")

    if result.metrics:
        p = plot_performance_summary(result.metrics)
        if p:
            paths.append(p)
            print(f"  绩效摘要: {p}")

    return paths
