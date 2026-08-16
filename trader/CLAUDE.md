# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

QMT (迅投) automated trading strategies and execution bridges. Two deployment modes:

- **Direct QMT strategies** — run inside the QMT client GUI, use `passorder` directly
- **Bridge mode** — QMT acts as a pure executor, receiving signals from a Mac-hosted web service via HTTP/WebSocket

## QMT code conventions (mandatory)

- First line must be `#coding:gbk`
- In `quickTrade=2` (immediate) mode, state MUST be stored in a plain global class instance (`class G: pass` / `g = G()`), never in `ContextInfo` attributes — context attributes get reset on bar rollback
- `timetag_to_datetime` and `passorder` are QMT built-in functions, not Python imports
- `get_trade_detail_data` with `'deal'` returns order fills; with `'position'` returns holdings; with `'order'` returns pending orders; with `'account'` returns cash/balance
- `ContextInfo.get_full_tick()` returns real-time quotes (`lastPrice`, `highLimit`, `lowLimit`)
- `ContextInfo.run_time()` is one-shot — must re-register inside the callback for recurring execution
- `ContextInfo.is_last_bar()` distinguishes historical replay bars from the current real-time bar in live mode

## Known QMT pitfalls

- **`passorder` return value**: `reqid = 0` does NOT always mean failure. Some broker versions (e.g., 国金证券) return 0 even when the order is successfully placed. Never use `reqid <= 0` as the sole failure check. Instead, poll `get_trade_detail_data(account, 'STOCK', 'deal')` or positions to confirm fills.
- **Strategy reload at market close**: When a strategy is loaded/reloaded after hours, QMT replays ALL historical 5-minute bars through `handlebar`. The final bar of the day passes `is_last_bar()` AND bar-time trading-hour filters, potentially triggering spurious orders. Always add a wall-clock time guard (`datetime.now()`) in addition to bar-time filtering.
- **Startup bar replay**: QMT pushes every historical bar stored locally (can be 10,000+) through `handlebar` on strategy start. Log only the first few skips and a final summary count; don't log every bar.

## QMT strategy files (direct mode)

### `JQ0707_QMT.py` — ETF momentum rotation

Daily rebalance at 11:00 via `run_time`. Calculates momentum scores (annualized return × R²) for 24 ETFs, holds the single highest-scoring ETF. Sells existing positions first, waits for fill confirmation via position polling, then buys the target.

Key config: `g.stock_sum`, `g.m_days`, `g.adjust_time`, `g.manual_capital`, `DEBUG_MODE`, `DRY_RUN`.

### `网格交易策略.py` — Grid trading (5-min bars)

Price-only grid trading driven by `handlebar` on 5-minute bars. When price crosses above a grid line → sell; crosses below → buy. Uses `quickTrade=2` for immediate execution. Confirms fills via `get_trade_detail_data(..., 'deal')` matching on `m_strRemark` prefix `网格:`.

Key config: `STOCKS`, `PRICE_RANGE_PCT`, `GRID_LEVELS`, `GRID_MODE`, `ORDER_SHARES`, `COOLDOWN_SECONDS`.

Anti-whipsaw protections (per-level cooldown, wall-clock guard, first-bar skip) were added after discovering the pitfalls listed above.

## Bridge mode files

### `qtrade_bridge.py` — 大QMT bridge strategy

Runs inside the QMT GUI. Uses `run_time` to periodically poll a Mac-hosted HTTP API (`/api/trade/pending`), executes received signals via `passorder`, and reports status back via HTTP PUT. Supports BUY, SELL, and CANCEL actions with both market and limit orders. Cancel matches by `m_strRemark` first, falls back to canceling all pending orders for the stock.

### `qmt_executor.py` — miniQMT standalone executor

Runs outside QMT as a standalone Python script on Windows. Connects to Mac via WebSocket for real-time signals, uses `xtquant` library to place orders through miniQMT, and reports status via HTTP.

## Supporting scripts

### `TraderTest.py`

Simulates the broker side. Polls cloud API for pending signals and can optionally mark them as `sent`/`cancelled`. Useful for testing the signal pipeline without a live QMT connection.

### `reset_pending.py`

Admin utility to reset signal status back to `pending` via the cloud API (JWT admin auth). Used when signals get stuck in `sent` state and need to be re-executed.

## Reference

- `example/QMT_API_快速开始.md` — Official QMT API quick-start (Chinese), documents `passorder` params, `run_time`, `handlebar`, `get_trade_detail_data`, and the three execution modes
- `logs/111.txt` — Example QMT order/trade log export (GBK-encoded TSV) for debugging fill behavior
