"""模拟成交引擎（PaperExecutor）。

消费一批 BUY/SELL 信号 → 取实时报价 → 算 A 股费率 → 改虚拟账户（cash +
``paper_holdings``，含 T+1 锁定）→ 写 ``paper_transactions`` + 回填
``paper_signals``。成交后盯盘，刷 ``last_price`` 并写 ``paper_equity_history``。

成本基础（avg_cost）含佣金；SELL 不改 avg_cost（按均价减仓），清仓时归零。
"""

from __future__ import annotations

from typing import Any

from src.costs import CostModel, compute_trade_cost
from src.paper import storage
from src.paper.fetchers import FallbackChain


class PaperExecutor:
    """单方案、单 tick 的模拟成交器。"""

    def __init__(
        self,
        plan: dict[str, Any],
        fetcher: FallbackChain,
        cost_model: CostModel | None = None,
    ):
        self.plan = plan
        self.plan_id = plan["id"]
        self.fetcher = fetcher
        self.cost_model = cost_model or CostModel()

    # ------------------------------------------------------------------
    # 批量成交入口
    # ------------------------------------------------------------------

    def execute_signals(
        self,
        signals: list[dict[str, Any]],
        tick_ts: str,
        trade_date: str,
    ) -> dict[str, int]:
        """对一批信号逐个模拟成交。

        一次性批量取价，再逐个成交。返回 ``{filled, rejected}`` 统计。
        任何单信号失败（取价失败/现金不足/T+1）只 reject 该信号，不影响其它。

        SELL 优先于 BUY 处理，确保卖出资金先到账再用于买入。
        """
        stats = {"filled": 0, "rejected": 0}
        if not signals:
            return stats

        # SELL first, BUY second — free up cash before spending
        signals = sorted(signals, key=lambda s: 0 if s["action"] == "SELL" else 1)

        # 一次批量取价（fallback 链）
        ts_codes = [s["ts_code"] for s in signals]
        prices = self.fetcher.fetch(ts_codes)

        for sig in signals:
            quote = prices.get(sig["ts_code"])
            if not quote or not quote["price"] or quote["price"] <= 0:
                self._reject(sig, f"取价失败/报价无效: {sig['ts_code']}")
                stats["rejected"] += 1
                continue

            price = quote["price"]
            src = quote["source"]
            cost = compute_trade_cost(
                sig["action"], sig["quantity"], price, self.cost_model
            )

            if sig["action"] == "BUY":
                ok = self._buy(sig, cost, src)
            else:
                ok = self._sell(sig, cost, src)

            if ok:
                stats["filled"] += 1
            else:
                stats["rejected"] += 1
        return stats

    # ------------------------------------------------------------------
    # BUY
    # ------------------------------------------------------------------

    def _buy(self, sig: dict[str, Any], cost, src: str) -> bool:
        plan = storage.get_plan(self.plan_id)
        if plan["cash"] < cost.net_amount:
            self._reject(
                sig,
                f"现金不足: 需 {cost.net_amount:.2f}, 现金 {plan['cash']:.2f}",
            )
            return False

        h = storage.get_holding(self.plan_id, sig["ts_code"])
        if h:
            old_basis = h["shares"] * h["avg_cost"]
            add_basis = cost.quantity * cost.fill_price + cost.commission
            new_shares = h["shares"] + cost.quantity
            new_t1 = h["t1_shares"] + cost.quantity
            new_free = h["free_shares"]
            new_avg = round((old_basis + add_basis) / new_shares, 4)
        else:
            new_shares = cost.quantity
            new_t1 = cost.quantity
            new_free = 0
            new_avg = round(
                (cost.quantity * cost.fill_price + cost.commission) / cost.quantity, 4
            )

        storage.upsert_holding(
            self.plan_id, sig["ts_code"], shares=new_shares,
            t1_shares=new_t1, free_shares=new_free, avg_cost=new_avg,
            last_price=cost.fill_price, last_price_src=src,
        )
        new_cash = round(plan["cash"] - cost.net_amount, 2)
        storage.update_plan_runtime(self.plan_id, cash=new_cash, clear_error=True)
        storage.append_transaction(self._tx_row(sig, cost, new_cash, src))
        storage.mark_filled(sig["id"], cost.fill_price, cost.quantity, src, sig["tick_ts"])
        return True

    # ------------------------------------------------------------------
    # SELL（T+1：只消耗 free_shares）
    # ------------------------------------------------------------------

    def _sell(self, sig: dict[str, Any], cost, src: str) -> bool:
        h = storage.get_holding(self.plan_id, sig["ts_code"])
        if not h or h["free_shares"] < cost.quantity:
            if not h:
                reason = "无持仓"
            else:
                reason = (f"T+1 锁定/可卖不足: 可卖 {h['free_shares']}, "
                          f"需 {cost.quantity}")
            self._reject(sig, f"{reason}: {sig['ts_code']}")
            return False

        plan = storage.get_plan(self.plan_id)
        new_free = h["free_shares"] - cost.quantity
        new_t1 = h["t1_shares"]
        new_shares = new_free + new_t1
        new_avg = h["avg_cost"] if new_shares > 0 else 0.0

        if new_shares == 0:
            # 清仓：先写 0 再删，保持 last_price 痕迹被清掉
            storage.delete_holding(self.plan_id, sig["ts_code"])
        else:
            storage.upsert_holding(
                self.plan_id, sig["ts_code"], shares=new_shares,
                t1_shares=new_t1, free_shares=new_free, avg_cost=new_avg,
                last_price=cost.fill_price, last_price_src=src,
            )

        new_cash = round(plan["cash"] + cost.net_amount, 2)
        storage.update_plan_runtime(self.plan_id, cash=new_cash, clear_error=True)
        storage.append_transaction(self._tx_row(sig, cost, new_cash, src))
        storage.mark_filled(sig["id"], cost.fill_price, cost.quantity, src, sig["tick_ts"])
        return True

    # ------------------------------------------------------------------
    # 盯盘 + 净值快照
    # ------------------------------------------------------------------

    def mark_to_market(self, tick_ts: str, trade_date: str) -> dict[str, Any] | None:
        """刷新所有持仓 ``last_price``，写一条净值快照。返回最新快照。"""
        holdings = storage.get_holdings(self.plan_id)
        ts_codes = [h["ts_code"] for h in holdings if h["shares"] > 0]
        prices = self.fetcher.fetch(ts_codes) if ts_codes else {}

        holdings_value = 0.0
        for h in holdings:
            if h["shares"] <= 0:
                continue
            q = prices.get(h["ts_code"])
            if q and q["price"] and q["price"] > 0:
                storage.update_holding_price(
                    self.plan_id, h["ts_code"], q["price"], q["source"], tick_ts
                )
                holdings_value += h["shares"] * q["price"]
            elif h["last_price"]:
                # 取不到价（停牌/源抽风）：冻结上一已知价
                holdings_value += h["shares"] * h["last_price"]

        plan = storage.get_plan(self.plan_id)
        cash = plan["cash"]
        total_equity = round(cash + holdings_value, 2)
        nav = round(total_equity / plan["total_capital"], 4) if plan["total_capital"] else 0.0

        prev = storage.get_latest_equity(self.plan_id)
        daily_return = None
        if prev and prev["total_equity"]:
            daily_return = round(
                (total_equity - prev["total_equity"]) / prev["total_equity"], 4
            )

        n_positions = sum(1 for h in holdings if h["shares"] > 0)
        storage.append_snapshot(
            self.plan_id, trade_date=trade_date, cash=round(cash, 2),
            holdings_value=round(holdings_value, 2), total_equity=total_equity,
            nav=nav, daily_return=daily_return, n_positions=n_positions,
            snapshot_ts=tick_ts,
        )
        return storage.get_latest_equity(self.plan_id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _tx_row(self, sig: dict[str, Any], cost, cash_after: float, src: str) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id, "signal_id": sig.get("id"),
            "ts_code": sig["ts_code"], "action": cost.action,
            "quantity": cost.quantity, "fill_price": cost.fill_price,
            "gross_amount": cost.gross_amount, "commission": cost.commission,
            "stamp_tax": cost.stamp_tax, "transfer_fee": cost.transfer_fee,
            "total_cost": cost.total_cost, "net_amount": cost.net_amount,
            "cash_after": cash_after, "price_source": src, "note": None,
        }

    def _reject(self, sig: dict[str, Any], reason: str) -> None:
        sig_id = sig.get("id")
        if sig_id is not None:
            storage.mark_rejected(sig_id, reason)
