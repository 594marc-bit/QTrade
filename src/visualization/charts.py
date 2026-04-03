"""Factor analysis visualization: IC timeseries, score distribution, top stocks, correlation heatmap."""

import platform
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.config import DATA_DIR, DEFAULT_WEIGHTS

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


# Apply font config on import
_setup_chinese_font()


def plot_ic_timeseries(
    ic_data: dict[str, pd.Series],
    save_path: Path | None = None,
) -> str:
    """Plot IC timeseries for all factors.

    Args:
        ic_data: Dict mapping factor_name → IC Series (indexed by trade_date).
        save_path: Override save path.

    Returns:
        Path to saved PNG file.
    """
    n_factors = len(ic_data)
    fig, axes = plt.subplots(n_factors, 1, figsize=(14, 3 * n_factors), sharex=True)
    if n_factors == 1:
        axes = [axes]

    colors = plt.cm.Set2(np.linspace(0, 1, max(n_factors, 2)))

    for ax, (name, ic_series), color in zip(axes, ic_data.items(), colors):
        valid = ic_series.dropna()
        if valid.empty:
            ax.text(0.5, 0.5, f"{name}: 数据不足", transform=ax.transAxes, ha="center")
            continue

        ax.bar(range(len(valid)), valid.values, color=color, alpha=0.6, width=1.0)
        ax.axhline(y=0, color="black", linewidth=0.5)
        ax.axhline(y=0.03, color="red", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(y=-0.03, color="red", linewidth=0.5, linestyle="--", alpha=0.5)
        ic_mean = valid.mean()
        ax.axhline(y=ic_mean, color=color, linewidth=1.0, linestyle="-", alpha=0.8,
                    label=f"均值: {ic_mean:+.4f}")
        ax.set_ylabel("IC", fontsize=10)
        ax.set_title(f"{name}", fontsize=11, fontweight="bold")
        ax.legend(loc="upper right", fontsize=9)

        # Show every 20th date label
        tick_positions = list(range(0, len(valid), max(1, len(valid) // 10)))
        tick_labels = [valid.index[i] for i in tick_positions]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, rotation=45, fontsize=8)

    axes[-1].set_xlabel("交易日期", fontsize=10)
    fig.suptitle("因子 IC 时序图", fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout()

    path = save_path or CHARTS_DIR / "ic_timeseries.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_score_distribution(
    df: pd.DataFrame,
    date: str | None = None,
    save_path: Path | None = None,
) -> str:
    """Plot total score distribution histogram for a given date.

    Args:
        df: DataFrame with trade_date, total_score columns.
        date: Target date string. Uses latest date if None.
        save_path: Override save path.

    Returns:
        Path to saved PNG file.
    """
    if date is None:
        date = df["trade_date"].max()

    day_df = df[df["trade_date"] == date]
    scores = day_df["total_score"].dropna()

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(scores, bins=30, color="#4C72B0", alpha=0.8, edgecolor="white")
    ax.axvline(x=scores.mean(), color="red", linestyle="--", linewidth=1.5,
               label=f"均值: {scores.mean():.2f}")
    ax.axvline(x=scores.median(), color="green", linestyle="--", linewidth=1.5,
               label=f"中位数: {scores.median():.2f}")
    ax.set_xlabel("综合评分", fontsize=12)
    ax.set_ylabel("股票数量", fontsize=12)
    ax.set_title(f"综合评分分布 ({date})", fontsize=14, fontweight="bold")
    ax.legend(fontsize=11)

    fig.tight_layout()
    path = save_path or CHARTS_DIR / "score_distribution.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_top10_stocks(
    df: pd.DataFrame,
    code2name: dict[str, str] | None = None,
    date: str | None = None,
    save_path: Path | None = None,
) -> str:
    """Plot Top 10 stocks as horizontal bar chart with factor contribution breakdown.

    Args:
        df: DataFrame with trade_date, ts_code, total_score, momentum_score, vol_ratio_score,
            volatility_score columns.
        code2name: Dict mapping ts_code → stock name.
        date: Target date. Uses latest if None.
        save_path: Override save path.

    Returns:
        Path to saved PNG file.
    """
    if date is None:
        date = df["trade_date"].max()

    day_df = df[df["trade_date"] == date].dropna(subset=["total_score"])
    top10 = day_df.nlargest(10, "total_score").iloc[::-1]  # reverse for horizontal bar

    labels = []
    for code in top10["ts_code"]:
        name = (code2name or {}).get(code, "")
        labels.append(f"{name} ({code})" if name else code)

    # Factor contributions — dynamically render from DEFAULT_WEIGHTS
    n = len(top10)
    score_to_label = {
        "momentum_score": ("动量", "#4C72B0"),
        "vol_score": ("量比", "#55A868"),
        "volatility_score": ("波动率", "#C44E52"),
        "rsi_score": ("RSI", "#8172B2"),
        "ma_deviation_score": ("MA偏离", "#CCB974"),
        "turnover_momentum_score": ("换手动量", "#64B5CD"),
        "intraday_range_score": ("日内波幅", "#937860"),
        "pe_ttm_rank_score": ("PE排名", "#E5AE38"),
        "pb_rank_score": ("PB排名", "#6D904F"),
    }

    fig, ax = plt.subplots(figsize=(12, 7))
    y_pos = range(len(labels))

    left = np.zeros(n)
    for score_col, weight in DEFAULT_WEIGHTS.items():
        if score_col in top10.columns:
            label, color = score_to_label.get(score_col, (score_col, "#999999"))
            contrib = top10[score_col].fillna(0).values * weight
            ax.barh(y_pos, contrib, left=left, color=color,
                    label=f"{label} ({weight:+.0%})", height=0.6)
            left += contrib

    # Total score labels
    for i, (_, row) in enumerate(top10.iterrows()):
        ax.text(row["total_score"] + 0.02, i, f'{row["total_score"]:.2f}',
                va="center", fontsize=10, fontweight="bold")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("综合评分", fontsize=12)
    ax.set_title(f"Top 10 股票评分 ({date})", fontsize=14, fontweight="bold")
    ax.legend(loc="lower right", fontsize=11)
    ax.axvline(x=0, color="black", linewidth=0.5)

    fig.tight_layout()
    path = save_path or CHARTS_DIR / "top10_stocks.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_factor_correlation(
    df: pd.DataFrame,
    factor_cols: list[str],
    date: str | None = None,
    save_path: Path | None = None,
) -> str:
    """Plot factor correlation heatmap.

    Args:
        df: DataFrame with factor columns.
        factor_cols: List of factor column names.
        date: Target date. Uses latest if None.
        save_path: Override save path.

    Returns:
        Path to saved PNG file.
    """
    if date is None:
        date = df["trade_date"].max()

    day_df = df[df["trade_date"] == date]
    valid_cols = [c for c in factor_cols if c in day_df.columns]
    corr = day_df[valid_cols].corr()

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")

    # Labels
    ax.set_xticks(range(len(valid_cols)))
    ax.set_yticks(range(len(valid_cols)))
    short_names = [c.replace("_20d", " 20日").replace("_ratio", "比") for c in valid_cols]
    ax.set_xticklabels(short_names, fontsize=10, rotation=45, ha="right")
    ax.set_yticklabels(short_names, fontsize=10)

    # Annotate cells
    for i in range(len(valid_cols)):
        for j in range(len(valid_cols)):
            val = corr.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=11, fontweight="bold",
                        color="white" if abs(val) > 0.5 else "black")

    fig.colorbar(im, ax=ax, label="相关系数")
    ax.set_title(f"因子相关性 ({date})", fontsize=14, fontweight="bold")

    fig.tight_layout()
    path = save_path or CHARTS_DIR / "factor_correlation.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def generate_all_charts(
    df: pd.DataFrame,
    ic_results: dict[str, dict],
    factor_cols: list[str],
    code2name: dict[str, str] | None = None,
) -> list[str]:
    """Generate all factor analysis charts.

    Args:
        df: DataFrame with all factor and score columns.
        ic_results: Dict from evaluate_factor(), keyed by factor name.
            Each value has 'ic_series' key.
        factor_cols: List of raw factor column names.
        code2name: Optional ts_code → name mapping.

    Returns:
        List of saved file paths.
    """
    saved = []

    # 1. IC timeseries
    ic_data = {name: result["ic_series"] for name, result in ic_results.items()}
    if len(ic_data) > 0:
        total_points = sum(len(s.dropna()) for s in ic_data.values())
        if total_points >= 10:
            path = plot_ic_timeseries(ic_data)
            saved.append(path)
            print(f"  IC 时序图: {path}")
        else:
            print("  ⚠️ IC 数据不足，跳过时序图")

    # 2. Score distribution
    if "total_score" in df.columns:
        path = plot_score_distribution(df)
        saved.append(path)
        print(f"  评分分布图: {path}")

    # 3. Top 10 stocks
    if "total_score" in df.columns:
        path = plot_top10_stocks(df, code2name)
        saved.append(path)
        print(f"  Top 10 图表: {path}")

    # 4. Factor correlation
    if len(factor_cols) >= 2:
        path = plot_factor_correlation(df, factor_cols)
        saved.append(path)
        print(f"  因子相关性图: {path}")

    return saved
