"""Scheme (preset) management for factor and weight configurations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.config import PROJECT_ROOT

SCHEMES_PATH = PROJECT_ROOT / "schemes.yaml"

_DEFAULT_TEMPLATE = """\
# QTrade 方案配置文件
# 每个方案包含: description (描述), factors (因子列表), weights (权重映射)
# 因子名使用注册名，权重键使用 score 列名

schemes:
  default:
    description: "默认方案"
    factors:
      - intraday_range_10d
      - pb_rank
      - pe_ttm_rank
      - trend_60d
      - volatility_20d
    weights:
      intraday_range_score: -0.15
      pb_rank_score: -0.25
      pe_ttm_rank_score: -0.20
      trend_score: -0.10
      volatility_score: -0.15

  conservative:
    description: "低估值稳健策略"
    factors:
      - pb_rank
      - pe_ttm_rank
      - trend_60d
      - volatility_20d
      - turnover_momentum_10d
    weights:
      pb_rank_score: -0.30
      pe_ttm_rank_score: -0.25
      trend_score: -0.15
      volatility_score: -0.15
      turnover_momentum_score: -0.15

  momentum:
    description: "动量反转策略"
    factors:
      - momentum_20d
      - vol_ratio
      - rsi_14d
      - volatility_20d
      - return_20d
    weights:
      momentum_score: 0.30
      vol_ratio_score: 0.20
      rsi_score: -0.25
      volatility_score: -0.15
      return_20d_score: 0.10
"""


def _read_yaml() -> dict[str, Any]:
    """Read and parse schemes.yaml. Returns empty dict if file missing."""
    if not SCHEMES_PATH.exists():
        return {}
    with open(SCHEMES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _write_yaml(data: dict[str, Any]) -> None:
    """Write data to schemes.yaml."""
    with open(SCHEMES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def ensure_schemes_file() -> None:
    """Create schemes.yaml with default template if it doesn't exist."""
    if not SCHEMES_PATH.exists():
        SCHEMES_PATH.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")


def load_schemes() -> dict[str, Any]:
    """Load all schemes from schemes.yaml.

    Returns:
        Dict mapping scheme names to their config dicts.
        Each config has 'description', 'factors', 'weights'.
        Returns empty dict if file missing or no schemes defined.
    """
    data = _read_yaml()
    return data.get("schemes", {})


def list_schemes() -> dict[str, str]:
    """List all available scheme names and their descriptions.

    Returns:
        Dict mapping scheme name to description string.
    """
    schemes = load_schemes()
    return {
        name: cfg.get("description", "")
        for name, cfg in schemes.items()
    }


def load_scheme(name: str) -> tuple[set[str], dict[str, float]]:
    """Load a single scheme by name.

    Args:
        name: Scheme name.

    Returns:
        Tuple of (enabled_factors, weights).

    Raises:
        ValueError: If scheme not found.
    """
    schemes = load_schemes()
    if name not in schemes:
        available = ", ".join(sorted(schemes.keys())) or "(无)"
        raise ValueError(f"方案 '{name}' 不存在。可用方案: {available}")

    cfg = schemes[name]
    factors = set(cfg.get("factors", []))
    weights = dict(cfg.get("weights", {}))

    # Validate factor names against registered factors
    factors, _invalid = _validate_factors(factors)
    if not factors:
        raise ValueError(f"方案 '{name}' 中无有效因子")

    return factors, weights


def _validate_factors(factors: set[str]) -> tuple[set[str], set[str]]:
    """Validate factor names against registered factors.

    Returns:
        Tuple of (valid_factors, invalid_factors).
    """
    from src.factors.base import get_registered_factors

    registered = set(get_registered_factors().keys())
    valid = factors & registered
    invalid = factors - registered

    if invalid:
        import sys
        print(f"  [yellow]警告: 以下因子不在注册列表中，已忽略: {', '.join(sorted(invalid))}[/yellow]",
              file=sys.stderr)

    return valid, invalid


def save_scheme(
    name: str,
    description: str,
    factors: set[str] | list[str],
    weights: dict[str, float],
) -> None:
    """Save a scheme to schemes.yaml.

    Creates the file if it doesn't exist. Overwrites existing scheme with
    the same name.

    Args:
        name: Scheme name.
        description: Scheme description.
        factors: Set or list of enabled factor names.
        weights: Dict mapping score column names to weight values.
    """
    ensure_schemes_file()
    data = _read_yaml()
    if "schemes" not in data:
        data["schemes"] = {}

    data["schemes"][name] = {
        "description": description,
        "factors": sorted(factors),
        "weights": weights,
    }
    _write_yaml(data)
