"""
qmt_executor.py — miniQMT 模式执行器（在 Windows 上运行）

通过 WebSocket 实时接收 Mac 端推送的交易信号，调用 xtquant 下单，
并通过 HTTP 回传订单状态。

用法:
    python qmt_executor.py
    python qmt_executor.py --config path/to/config_windows.json

依赖:
    pip install websockets httpx xtquant
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config_windows.json"


def load_config(path: Path | None = None) -> dict:
    """Load configuration from JSON file."""
    if path is None:
        path = CONFIG_PATH

    if not path.exists():
        print(f"[ERROR] Config file not found: {path}")
        print("Copy config_windows.example.json to config_windows.json and edit it.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# xtquant wrapper (imported lazily — only on Windows)
# ---------------------------------------------------------------------------

_xt_trader = None


def _init_xtquant():
    """Initialize xtquant connection to miniQMT."""
    global _xt_trader
    try:
        from xtquant import xtdata, xttrader, xtconstant

        # xtdata.download_history_data() may be called first to ensure
        # miniQMT has the required data, but for pure execution we skip that.

        session_id = int(time.time())
        xt_trader = xttrader.XtQuantTrader(
            str(SCRIPT_DIR / "miniQMT"),  # miniQMT 文件路径
            session_id,
        )
        xt_trader.start()
        connect_result = xt_trader.connect()

        if connect_result != 0:
            print(f"[ERROR] miniQMT connect failed: {connect_result}")
            return None

        # Subscribe to account
        accounts = xt_trader.query_account()
        if accounts:
            xt_trader.subscribe(accounts[0].account_id)
            print(f"[miniQMT] Connected, account: {accounts[0].account_id}")
        else:
            print("[miniQMT] Connected (no account queried)")

        _xt_trader = xt_trader
        return xt_trader
    except ImportError:
        print("[WARN] xtquant not available — running in dry-run mode")
        return None
    except Exception as e:
        print(f"[ERROR] xtquant init failed: {e}")
        return None


def _execute_signal(signal: dict) -> str | None:
    """Place an order via xtquant. Returns broker_order_id or None."""
    if _xt_trader is None:
        print(f"[DRY-RUN] {signal['ts_code']} {signal['action']} "
              f"{signal['quantity']}股")
        return f"dry-{int(time.time())}"

    try:
        from xtquant import xtconstant

        if signal["action"] == "BUY":
            order_type = xtconstant.STOCK_BUY
        else:
            order_type = xtconstant.STOCK_SELL

        # Market price order
        price_type = xtconstant.LATEST_PRICE  # 最新价

        order_id = _xt_trader.order_stock(
            stock_code=signal["ts_code"],
            order_type=order_type,
            order_volume=signal["quantity"],
            price_type=price_type,
            price=0,  # 市价单价格为0
            strategy_name="qtrade",
            order_remark=f"rebalance_{signal.get('rebalance_date', '')}",
        )

        print(f"[miniQMT] Order placed: {signal['ts_code']} "
              f"{signal['action']} {signal['quantity']}股 → {order_id}")
        return str(order_id)
    except Exception as e:
        print(f"[ERROR] xtquant order failed: {e}")
        raise


# ---------------------------------------------------------------------------
# HTTP helpers (for status updates)
# ---------------------------------------------------------------------------

def _http_put(url: str, body: dict) -> bool:
    """Simple HTTP PUT with httpx or urllib fallback."""
    try:
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[ERROR] HTTP PUT {url}: {e}")
        return False


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------

def _build_ws_url(cfg: dict) -> str:
    """Build WebSocket URL from config."""
    http_host = cfg["mac_host"].rstrip("/")
    ws_host = http_host.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_host}/ws/live?api_key={cfg['api_key']}"


def ws_connect_and_listen(cfg: dict):
    """Connect to Mac WebSocket server and process signals.

    Runs in a loop with exponential backoff reconnect.
    """
    from websockets.sync.client import connect

    ws_url = _build_ws_url(cfg)
    backoff = 1

    while True:
        try:
            print(f"[WS] Connecting to {cfg['mac_host']}...")
            with connect(ws_url, open_timeout=10) as ws:
                print(f"[WS] Connected!")
                backoff = 1  # reset on successful connection

                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "signals":
                        _handle_signals(msg.get("data", []))
                    elif msg.get("type") == "pong":
                        pass  # heartbeat response
                    elif msg.get("error"):
                        print(f"[WS] Server error: {msg['error']}")
        except Exception as e:
            print(f"[WS] Disconnected: {e}")
            print(f"[WS] Reconnecting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)  # cap at 60s

        # Catch up on any missed signals
        _catch_up_missed(cfg)


def _handle_signals(signals: list[dict]):
    """Process incoming signals: execute and report status."""
    for sig in signals:
        try:
            order_id = _execute_signal(sig)
            _http_put(
                f"{cfg['mac_host']}/api/trade/{sig['id']}/status",
                {"status": "sent", "broker_order_id": order_id},
            )

            if sig.get("price_type") == "MKT":
                # Market orders fill immediately → mark filled
                # (In practice, xtquant callbacks should handle this)
                _http_put(
                    f"{cfg['mac_host']}/api/trade/{sig['id']}/status",
                    {"status": "filled", "filled_qty": sig["quantity"]},
                )
        except Exception as e:
            print(f"[ERROR] Signal {sig.get('id')}: {e}")
            _http_put(
                f"{cfg['mac_host']}/api/trade/{sig['id']}/status",
                {"status": "rejected", "error_msg": str(e)},
            )


def _catch_up_missed(cfg: dict):
    """Fetch any pending signals via REST API after reconnect."""
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{cfg['mac_host']}/api/trade/pending",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            missed = json.loads(resp.read().decode())
        if missed:
            print(f"[WS] Caught up: {len(missed)} missed signal(s)")
            _handle_signals(missed)
    except Exception as e:
        print(f"[ERROR] Catch-up failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

cfg: dict = {}  # global for HTTP helpers


def main():
    global cfg
    cfg = load_config()

    print("=" * 50)
    print("  QTrade miniQMT Executor")
    print(f"  Mac: {cfg['mac_host']}")
    print(f"  Mode: {'DRY-RUN' if _init_xtquant() is None else 'LIVE'}")
    print("=" * 50)

    ws_connect_and_listen(cfg)


if __name__ == "__main__":
    main()
