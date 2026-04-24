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

    parser.add_argument("--index", type=str, help="股票指数代码 (000300/000905/000852/all)")
    parser.add_argument("--start", type=str, help="起始日期 (YYYYMMDD)")
    parser.add_argument("--end", type=str, help="结束日期 (YYYYMMDD)")
    parser.add_argument("--source", type=str, choices=["akshare", "tushare"], help="数据源")
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
    parser.add_argument("--yes", "-y", action="store_true", help="跳过所有确认提示，自动执行回测")

    args = parser.parse_args()

    # Build CLI args dict (only non-None values)
    cli_args = {k: v for k, v in vars(args).items() if v is not None}

    # Determine mode — scheme can also enable full CLI mode
    has_any_arg = bool(cli_args)
    has_scheme = "scheme" in cli_args
    is_full_cli = (
        all(k in cli_args for k in ["index", "start", "end", "source"])
        and ("factors" in cli_args or has_scheme)
    )

    if is_full_cli:
        # Full command-line mode — no interaction needed
        from src.cli.wizard import WizardConfig, run_pipeline
        from src.scheme import load_scheme, list_schemes

        cfg = WizardConfig()
        cfg.index_code = cli_args["index"]
        cfg.start_date = cli_args["start"]
        cfg.end_date = cli_args["end"]
        cfg.data_source = cli_args["source"]

        # Load scheme if specified
        scheme_factors = None
        scheme_weights = None
        if has_scheme:
            scheme_name = cli_args["scheme"]
            try:
                scheme_factors, scheme_weights = load_scheme(scheme_name)
            except ValueError as e:
                print(f"错误: {e}")
                sys.exit(1)

        # Factors: --factors takes priority over scheme
        if "factors" in cli_args:
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

        run_pipeline(cfg)
    else:
        # Interactive or mixed mode
        from src.cli.wizard import run_wizard
        run_wizard(cli_args if has_any_arg else None)


if __name__ == "__main__":
    main()
