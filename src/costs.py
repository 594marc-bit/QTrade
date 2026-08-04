"""A 股交易成本模型（共享模块）。

统一佣金/印花税计算，paper trading 使用；语义对齐 ``src/grid/grid_params.py``
的费率常量，并补上 A 股单笔最低佣金（5 元）兜底。

设计取舍：``src/grid/`` 维持现状不动（仅复用其费率数值语义），本模块供
paper（以及未来可选地供 backtest）调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["BUY", "SELL"]


@dataclass
class CostModel:
    """A 股交易费率。"""

    buy_commission_rate: float = 0.0003   # 买入佣金
    sell_commission_rate: float = 0.0003  # 卖出佣金
    stamp_tax_rate: float = 0.0005        # 印花税（仅卖出）
    transfer_fee_rate: float = 0.0        # 过户费（沪市，v1 占位 0）
    min_commission: float = 5.0           # 单笔最低佣金


@dataclass
class CostBreakdown:
    """单笔成交的费率拆分。"""

    action: Action
    quantity: int
    fill_price: float
    gross_amount: float   # quantity × fill_price
    commission: float     # max(rate × gross, min_commission)
    stamp_tax: float      # 仅 SELL
    transfer_fee: float
    total_cost: float     # commission + stamp_tax + transfer_fee
    net_amount: float     # BUY: gross + total_cost；SELL: gross - total_cost


def compute_trade_cost(
    action: Action,
    quantity: int,
    fill_price: float,
    model: CostModel | None = None,
) -> CostBreakdown:
    """计算一笔 A 股成交的费用。

    Args:
        action: "BUY" 或 "SELL"。
        quantity: 成交股数。
        fill_price: 成交价。
        model: 费率模型，默认 :class:`CostModel`。

    Returns:
        :class:`CostBreakdown`，含佣金/印花税/总成本/净额。

    Raises:
        ValueError: action 非 BUY/SELL。

    最低佣金规则：单笔佣金按 ``max(费率×gross, 5.0)`` 计提；零股/零价视为
    无成交，返回全 0 拆分。
    """
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action 必须是 BUY 或 SELL，收到 {action!r}")

    model = model or CostModel()
    gross = round(quantity * fill_price, 2)

    # 零成交边界：无费用
    if quantity <= 0 or fill_price <= 0 or gross <= 0:
        return CostBreakdown(
            action=action, quantity=quantity, fill_price=fill_price,
            gross_amount=gross, commission=0.0, stamp_tax=0.0,
            transfer_fee=0.0, total_cost=0.0, net_amount=gross,
        )

    if action == "BUY":
        commission = max(gross * model.buy_commission_rate, model.min_commission)
        stamp_tax = 0.0
    else:  # SELL
        commission = max(gross * model.sell_commission_rate, model.min_commission)
        stamp_tax = gross * model.stamp_tax_rate

    transfer_fee = gross * model.transfer_fee_rate
    total_cost = commission + stamp_tax + transfer_fee
    net = gross + total_cost if action == "BUY" else gross - total_cost

    # 金额按分（0.01）四舍五入，避免浮点误差、与券商实际计提一致
    return CostBreakdown(
        action=action, quantity=quantity, fill_price=fill_price,
        gross_amount=gross, commission=round(commission, 2),
        stamp_tax=round(stamp_tax, 2), transfer_fee=round(transfer_fee, 2),
        total_cost=round(total_cost, 2), net_amount=round(net, 2),
    )
