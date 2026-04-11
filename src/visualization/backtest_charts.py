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


def _outdir(output_dir: Path | None) -> Path:
    """Resolve output directory, creating it if needed."""
    d = output_dir or CHARTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def plot_nav_curve(nav_series: pd.DataFrame, save: bool = True, output_dir: Path | None = None) -> str | None:
    """Plot strategy NAV vs benchmark NAV."""
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

    d = _outdir(output_dir)
    path = d / "backtest_nav.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_drawdown(nav_series: pd.DataFrame, save: bool = True, output_dir: Path | None = None) -> str | None:
    """Plot drawdown area chart with max drawdown annotation."""
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]
    peak = nav.cummax()
    drawdown = (nav - peak) / peak

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(drawdown.index, drawdown.values, 0, color="#FF5252", alpha=0.4)
    ax.plot(drawdown.index, drawdown.values, color="#FF5252", linewidth=0.8)

    min_idx = drawdown.idxmin()
    min_val = drawdown.min()
    ax.annotate(
        f"最大回撤: {min_val:.2%}",
        xy=(min_idx, min_val),
        xytext=(min_idx, min_val * 0.5),
        fontsize=11, color="#D32F2F", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#D32F2F"),
    )

    ax.set_title("历史回撤", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=12)
    ax.set_ylabel("回撤", fontsize=12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    d = _outdir(output_dir)
    path = d / "backtest_drawdown.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_performance_summary(metrics: dict, save: bool = True, output_dir: Path | None = None) -> str | None:
    """Plot performance metrics summary table."""
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
        cellLoc="center", loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.5)

    ax.set_title("回测绩效摘要", fontsize=16, fontweight="bold", pad=20)

    d = _outdir(output_dir)
    path = d / "backtest_summary.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_monthly_returns(nav_series: pd.DataFrame, save: bool = True, output_dir: Path | None = None) -> str | None:
    """Plot monthly returns heatmap."""
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]

    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 2:
        return None

    monthly_ret = monthly.pct_change().dropna()

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

    for i in range(len(pivot)):
        for j in range(12):
            j_idx = j + 1
            if j_idx in pivot.columns:
                val = pivot.iloc[i].get(j_idx, np.nan)
                if not np.isnan(val):
                    color = "white" if abs(val) > 0.08 else "black"
                    ax.text(j, i, f"{val:.1%}", ha="center", va="center", fontsize=9, color=color)

    ax.set_title("月度收益率热力图", fontsize=16, fontweight="bold")
    fig.colorbar(im, ax=ax, label="月度收益率")

    d = _outdir(output_dir)
    path = d / "backtest_monthly_returns.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_trade_log(
    nav_series: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    save: bool = True,
    output_dir: Path | None = None,
) -> str | None:
    """Plot NAV curve with buy/sell trade markers."""
    fig, ax = plt.subplots(figsize=(14, 6))

    dates = pd.to_datetime(nav_series["date"])
    ax.plot(dates, nav_series["nav"], label="策略净值", linewidth=1.5, color="#2196F3")

    if "benchmark_nav" in nav_series.columns and not nav_series["benchmark_nav"].isna().all():
        ax.plot(
            dates, nav_series["benchmark_nav"],
            label="沪深300", linewidth=1.2, color="#9E9E9E", linestyle="--",
        )

    # Mark trades on the NAV curve
    if trades is not None and not trades.empty:
        trade_dates = pd.to_datetime(trades["date"])
        buys = trades[trades["action"] == "buy"]
        sells = trades[trades["action"] == "sell"]

        if not buys.empty:
            ax.scatter(
                trade_dates.loc[buys.index], buys["price"].values,
                marker="^", color="#4CAF50", s=20, label="买入", zorder=5, alpha=0.7,
            )
        if not sells.empty:
            ax.scatter(
                trade_dates.loc[sells.index], sells["price"].values,
                marker="v", color="#FF5252", s=20, label="卖出", zorder=5, alpha=0.7,
            )

    ax.set_title("交易记录", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=12)
    ax.set_ylabel("价格", fontsize=12)
    ax.legend(fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()

    d = _outdir(output_dir)
    path = d / "backtest_trade_log.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_return_distribution(
    nav_series: pd.DataFrame,
    save: bool = True,
    output_dir: Path | None = None,
) -> str | None:
    """Plot daily return distribution histogram with normal distribution overlay."""
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]
    daily_ret = nav.pct_change().dropna()

    if len(daily_ret) < 30:
        return None

    from scipy.stats import norm

    fig, ax = plt.subplots(figsize=(10, 6))

    # Histogram
    mu, sigma = daily_ret.mean(), daily_ret.std()
    bins = min(60, int(len(daily_ret) ** 0.5) * 2)
    ax.hist(daily_ret, bins=bins, density=True, alpha=0.7, color="#2196F3", label="日度收益率")

    # Normal overlay
    x = np.linspace(daily_ret.min(), daily_ret.max(), 200)
    ax.plot(x, norm.pdf(x, mu, sigma), "r--", linewidth=1.5, label=f"正态分布 (μ={mu:.4f}, σ={sigma:.4f})")

    # Statistics
    skew = daily_ret.skew()
    kurtosis = daily_ret.kurtosis()
    stats_text = f"均值: {mu:.4f}  标准差: {sigma:.4f}\n偏度: {skew:.4f}  峰度: {kurtosis:.4f}"
    ax.text(
        0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
        verticalalignment="top", horizontalalignment="right",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    ax.set_title("日度收益分布", fontsize=16, fontweight="bold")
    ax.set_xlabel("日度收益率", fontsize=12)
    ax.set_ylabel("概率密度", fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)

    d = _outdir(output_dir)
    path = d / "backtest_return_distribution.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def plot_yearly_returns(
    nav_series: pd.DataFrame,
    save: bool = True,
    output_dir: Path | None = None,
) -> str | None:
    """Plot yearly returns as bar chart (green=positive, red=negative)."""
    nav = nav_series.set_index(pd.to_datetime(nav_series["date"]))["nav"]
    yearly = nav.resample("YE").last().dropna()
    if len(yearly) < 1:
        return None

    yearly_ret = yearly.pct_change().dropna()
    if yearly_ret.empty:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    years = yearly_ret.index.year.astype(str)
    colors = ["#4CAF50" if r > 0 else "#FF5252" for r in yearly_ret]

    bars = ax.bar(years, yearly_ret.values, color=colors, edgecolor="white", linewidth=0.5)

    for bar, val in zip(bars, yearly_ret):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height(),
            f"{val:.1%}", ha="center", va="bottom", fontsize=10, fontweight="bold",
        )

    ax.set_title("年度收益率", fontsize=16, fontweight="bold")
    ax.set_xlabel("年份", fontsize=12)
    ax.set_ylabel("收益率", fontsize=12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(True, alpha=0.3, axis="y")

    d = _outdir(output_dir)
    path = d / "backtest_yearly_returns.png" if save else None
    if save:
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path) if path else None


def generate_backtest_charts(result, output_dir: Path | None = None) -> list[str]:
    """Generate all backtest visualization charts.

    Args:
        result: BacktestResult object.
        output_dir: Custom output directory. Defaults to CHARTS_DIR.

    Returns:
        List of saved chart paths.
    """
    paths = []

    if result.nav_series is not None and not result.nav_series.empty:
        p = plot_nav_curve(result.nav_series, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  净值曲线: {p}")

        p = plot_drawdown(result.nav_series, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  回撤图: {p}")

        p = plot_monthly_returns(result.nav_series, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  月度收益: {p}")

        # New charts
        p = plot_trade_log(result.nav_series, trades=result.trades, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  交易记录: {p}")

        p = plot_return_distribution(result.nav_series, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  收益分布: {p}")

        p = plot_yearly_returns(result.nav_series, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  年度收益: {p}")

    if result.metrics:
        p = plot_performance_summary(result.metrics, output_dir=output_dir)
        if p:
            paths.append(p)
            print(f"  绩效摘要: {p}")

    return paths
