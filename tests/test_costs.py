"""Tests for src/costs.py — A 股交易成本模型。"""

import pytest

from src.costs import CostModel, compute_trade_cost


def test_buy_basic_floor_applies():
    """1000 股 × 10 元：gross 10000，佣金按费率 3 元 < 5 元 → 取最低 5 元。"""
    bd = compute_trade_cost("BUY", 1000, 10.0)
    assert bd.gross_amount == 10000.0
    assert bd.commission == 5.0          # floor
    assert bd.stamp_tax == 0.0           # 买无印花税
    assert bd.transfer_fee == 0.0
    assert bd.total_cost == 5.0
    assert bd.net_amount == 10005.0      # gross + cost


def test_sell_basic_floor_applies():
    """卖出 1000 股 × 10 元：佣金 5（兜底）+ 印花税 5。"""
    bd = compute_trade_cost("SELL", 1000, 10.0)
    assert bd.gross_amount == 10000.0
    assert bd.commission == 5.0
    assert bd.stamp_tax == 5.0           # 10000 × 0.0005
    assert bd.total_cost == 10.0
    assert bd.net_amount == 9990.0       # gross - cost


def test_buy_above_floor():
    """大单：gross 100000，佣金 30 元 > 5 元，按费率。"""
    bd = compute_trade_cost("BUY", 10000, 10.0)
    assert bd.commission == 30.0
    assert bd.stamp_tax == 0.0
    assert bd.net_amount == 100030.0


def test_sell_above_floor():
    """大单卖出：佣金 30 + 印花税 50。"""
    bd = compute_trade_cost("SELL", 10000, 10.0)
    assert bd.commission == 30.0
    assert bd.stamp_tax == 50.0
    assert bd.total_cost == 80.0
    assert bd.net_amount == 99920.0


def test_min_commission_floor_tiny_order():
    """1 股 × 10 元：gross 10，佣金 max(0.003, 5) = 5。"""
    bd = compute_trade_cost("BUY", 1, 10.0)
    assert bd.commission == 5.0
    assert bd.gross_amount == 10.0
    assert bd.net_amount == 15.0


def test_zero_quantity_returns_zero_costs():
    """零股/零价边界：无成交，全 0。"""
    bd = compute_trade_cost("BUY", 0, 10.0)
    assert bd.commission == 0.0
    assert bd.stamp_tax == 0.0
    assert bd.total_cost == 0.0
    assert bd.net_amount == 0.0


def test_invalid_action_raises():
    with pytest.raises(ValueError):
        compute_trade_cost("HOLD", 100, 10.0)


def test_custom_model_overrides_rates():
    """自定义费率模型生效。"""
    m = CostModel(buy_commission_rate=0.001, min_commission=10.0)
    bd = compute_trade_cost("BUY", 10000, 10.0, model=m)  # gross 100000
    assert bd.commission == 100.0          # 100000 × 0.001
