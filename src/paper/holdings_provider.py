"""``paper_holdings`` → ``SignalGenerator`` 的 holdings_provider 适配器。

paper 路径下 diff 应对**实际虚拟持仓**（``free_shares + t1_shares``），而非
live 路径的目标态快照。返回与 ``portfolio_snapshots`` 兼容的 DataFrame
（含 ``ts_code`` + ``target_shares``），使 :meth:`_diff_portfolio` 无需改动。

SELL 数量按总持仓计提；T+1 锁定（``t1_shares`` 当日不可卖）由 executor 在
成交时强制——diff 只负责"该卖什么"，executor 负责"今天能不能卖"。
"""

from __future__ import annotations

import pandas as pd

from src.paper import storage


def make_paper_holdings_provider(plan_id: int):
    """返回一个 ``() -> pd.DataFrame`` 的 callable，供 SignalGenerator 注入。"""

    def _provide() -> pd.DataFrame:
        rows = storage.get_holdings(plan_id)
        if not rows:
            return pd.DataFrame(columns=["ts_code", "target_shares"])
        return pd.DataFrame([
            {
                "ts_code": r["ts_code"],
                "target_shares": (r["free_shares"] or 0) + (r["t1_shares"] or 0),
            }
            for r in rows
        ])

    return _provide
