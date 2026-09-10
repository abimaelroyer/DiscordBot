#!/usr/bin/env python3
"""Companion MCP server exposing AlgoTrader's strategy signals as agent tools.

Robinhood's own MCP (https://agent.robinhood.com/mcp/trading) handles account
data and order execution. This server complements it with the deterministic
strategy logic Robinhood's MCP does not provide:

  * Wilder RSI(14) computed server-side from intraday candles
  * stop-loss / take-profit exit signals against recorded entry prices
  * local open-position tracking and realised PnL

Division of labour: Robinhood's MCP PLACES the orders. After each fill, the
agent calls record_buy / record_sell here so this server's position ledger and
realised PnL stay in sync. Execution and bookkeeping are deliberately separate,
so the risk math (RSI, stops) stays deterministic instead of estimated.

STATE ISOLATION: this server keeps its own ledger under state/agent/, SEPARATE
from the automated loop's state/positions.json. The loop trades the main
Robinhood account; the agent trades the segregated *agentic* account. Sharing
one ledger would cross the two accounts' positions -- so they are kept apart,
and the agent ledger starts empty.

Market data: get_rsi / scan_universe / get_exit_signals fetch candles via the
existing robinhood.get_historical_close_prices (extended hours, span='day'),
using the Robinhood read-only login session -- no order scope. To drop that
dependency, swap _fetch_closes(); it is the single point of change. (Note a
regular-session source like yfinance yields a different RSI window.)

Transport: stdio by default (Claude Code / Desktop). Set
STRATEGY_MCP_TRANSPORT=http for Streamable HTTP (Messages API MCP connector).
"""

import os

# strategy.py / reflect.py use paths relative to the project root. Anchor the
# working directory to this file's folder so they resolve no matter where the
# MCP host launches the process.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import sys
import json
import functools
import contextlib
import datetime

from mcp.server.mcpserver import MCPServer

import strategy
import robinhood

mcp = MCPServer("algotrader-strategy")

DEFAULT_INTERVAL = "5minute"

# --- Agent-only state, isolated from the automated loop's state/ files --------
AGENT_STATE_DIR = os.path.join("state", "agent")
POSITIONS_PATH = os.path.join(AGENT_STATE_DIR, "positions.json")
COOLDOWNS_PATH = os.path.join(AGENT_STATE_DIR, "cooldowns.json")
REALIZED_PATH = os.path.join(AGENT_STATE_DIR, "realized.json")


def _tool(fn):
    """Register an MCP tool with its stdout redirected to stderr.

    strategy.py / robinhood.py print status lines (the Robinhood login
    handshake, "Universe: N tickers", realised-PnL notices, ...) to stdout. In a
    stdio MCP server stdout IS the JSON-RPC channel, so those prints would
    corrupt the protocol framing. Send anything a tool writes to stderr instead.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            return fn(*args, **kwargs)
    return mcp.tool()(wrapper)


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fetch_closes(symbol: str) -> list:
    """Return the intraday close-price series for a symbol (oldest first).

    Single point of change to switch market-data sources.
    """
    return robinhood.get_historical_close_prices(symbol, interval=DEFAULT_INTERVAL, span="day")


@_tool
def get_rsi(symbol: str) -> dict:
    """Wilder RSI(14) and latest price for one ticker, from intraday 5-minute candles.

    Use this tool instead of estimating RSI yourself. rsi is None when there are
    too few bars to compute it.
    """
    symbol = symbol.upper().strip()
    closes = _fetch_closes(symbol)
    if not closes:
        return {"symbol": symbol, "rsi": None, "price": None, "bars": 0, "error": "no data"}
    return {
        "symbol": symbol,
        "rsi": strategy.calculate_rsi(closes),
        "price": closes[-1],
        "bars": len(closes),
    }


@_tool
def scan_universe() -> dict:
    """RSI for every ticker in the pinned universe, flagged as entry/exit candidates.

    Thresholds come from strategy_params.json. Results are sorted most-oversold
    first and each is tagged oversold / overbought / neutral. Note: this fetches
    candles for every ticker sequentially, so it is slow for a large universe.
    """
    params = strategy.load_params("strategy_params.json")
    oversold = params.get("rsi_oversold_threshold", 30.0)
    overbought = params.get("rsi_overbought_threshold", 70.0)
    universe = strategy.get_trading_universe(params)

    results = []
    for sym in universe:
        try:
            closes = _fetch_closes(sym)
            if not closes:
                continue
            rsi = strategy.calculate_rsi(closes)
            if rsi is None:
                continue
            tag = "oversold" if rsi <= oversold else "overbought" if rsi >= overbought else "neutral"
            results.append({"symbol": sym, "rsi": round(rsi, 2), "price": closes[-1], "signal": tag})
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    results.sort(key=lambda r: r.get("rsi", 999))
    return {
        "oversold_threshold": oversold,
        "overbought_threshold": overbought,
        "count": len(results),
        "tickers": results,
    }


@_tool
def should_enter(symbol: str) -> dict:
    """Full entry check for one ticker: RSI vs oversold threshold + dedup + cooldown.

    Dedup/cooldown are checked against THIS server's agent ledger (the agentic
    account), not the automated loop's state. enter=True only when the strategy
    would open a new position.
    """
    symbol = symbol.upper().strip()
    params = strategy.load_params("strategy_params.json")
    oversold = params.get("rsi_oversold_threshold", 30.0)

    closes = _fetch_closes(symbol)
    if not closes:
        return {"symbol": symbol, "enter": False, "reason": "no data"}

    rsi = strategy.calculate_rsi(closes)
    if rsi is None:
        return {"symbol": symbol, "enter": False, "reason": "insufficient bars"}
    if symbol in _read_json(POSITIONS_PATH, {}):
        return {"symbol": symbol, "enter": False, "rsi": round(rsi, 2), "reason": "position already open"}
    if _in_cooldown(symbol):
        return {"symbol": symbol, "enter": False, "rsi": round(rsi, 2), "reason": "re-entry cooldown"}
    if rsi > oversold:
        return {"symbol": symbol, "enter": False, "rsi": round(rsi, 2),
                "reason": f"RSI {rsi:.2f} above oversold threshold {oversold}"}

    return {
        "symbol": symbol,
        "enter": True,
        "rsi": round(rsi, 2),
        "price": closes[-1],
        "suggested_dollars": strategy.ORDER_DOLLARS,
    }


@_tool
def get_exit_signals() -> dict:
    """Check every OPEN tracked position against stop-loss / take-profit rules.

    Reads stop_loss_pct / take_profit_pct from state/strategy.yaml and entry
    prices from the agent ledger. Returns which positions should be SOLD and
    why. It does NOT place any order -- place sells via the Robinhood MCP, then
    call record_sell here to close them out locally.
    """
    stop_loss_pct, take_profit_pct = strategy.load_exit_rules()
    positions = _read_json(POSITIONS_PATH, {})

    signals = []
    for sym, pos in positions.items():
        entry = float(pos.get("entry_price", 0) or 0)
        if entry <= 0:
            continue
        try:
            closes = _fetch_closes(sym)
            if not closes:
                continue
            price = closes[-1]
            change_pct = (price - entry) / entry * 100.0
            reason = None
            if stop_loss_pct is not None and change_pct <= -abs(stop_loss_pct):
                reason = "STOP_LOSS"
            elif take_profit_pct is not None and change_pct >= abs(take_profit_pct):
                reason = "TAKE_PROFIT"
            signals.append({
                "symbol": sym,
                "entry_price": entry,
                "price": price,
                "change_pct": round(change_pct, 2),
                "action": "SELL" if reason else "HOLD",
                "reason": reason,
            })
        except Exception as e:
            signals.append({"symbol": sym, "error": str(e)})

    return {
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "positions_checked": len(positions),
        "signals": signals,
    }


@_tool
def get_open_positions() -> dict:
    """The agent's ledger of open positions (symbol, entry price, quantity, timestamp)."""
    return {"positions": _read_json(POSITIONS_PATH, {})}


@_tool
def get_pnl() -> dict:
    """Realised PnL over closed round-trips: count, win rate, average return, total dollars."""
    closed = _read_json(REALIZED_PATH, [])
    if not closed:
        return {"closed_trades": 0, "message": "no closed trades yet"}
    returns = [float(t.get("realised_return", 0.0)) for t in closed]
    dollars = [float(t.get("realised_dollars", 0.0)) for t in closed]
    wins = sum(1 for r in returns if r > 0)
    return {
        "closed_trades": len(closed),
        "win_rate": round(wins / len(returns), 3),
        "avg_return": round(sum(returns) / len(returns), 4),
        "total_realised_dollars": round(sum(dollars), 2),
    }


@_tool
def run_optimizer() -> dict:
    """Tune RSI thresholds from the agent's realised PnL and update strategy_params.json.

    This is the Hermes learning loop for the agent path. It reads closed trades
    from the agent ledger (state/agent/realized.json) and, once there are at
    least 5, TIGHTENS thresholds when the average realised return is <= 0 and
    RELAXES them when it is positive (both clamped). Below 5 closed trades it
    holds steady rather than tuning on noise. Every change is logged to
    state/agent/hypotheses.json. Call this at the END of a trading cycle so the
    next scan_universe / should_enter uses the tuned values.
    """
    params_path = "strategy_params.json"
    params = strategy.load_params(params_path)
    closed = _read_json(REALIZED_PATH, [])
    n = len(closed)

    old_os = params.get("rsi_oversold_threshold", 30.0)
    old_ob = params.get("rsi_overbought_threshold", 70.0)
    new_os, new_ob = old_os, old_ob
    metrics = {"closed_trades": n}

    if n < 5:
        reasoning = f"Only {n} closed trade(s); insufficient realised PnL to tune. Holding steady."
    else:
        returns = [float(t.get("realised_return", 0.0)) for t in closed]
        avg = sum(returns) / len(returns)
        wins = sum(1 for r in returns if r > 0)
        metrics.update({"avg_realised_return": avg, "win_rate": wins / len(returns)})
        if avg <= 0:
            new_os = max(20.0, old_os - 1.0)
            new_ob = min(80.0, old_ob + 1.0)
            reasoning = (f"Avg realised return {avg * 100:.2f}% over {n} trades "
                         f"(win rate {wins / len(returns) * 100:.0f}%); tightening entry, loosening exit.")
        else:
            new_os = min(35.0, old_os + 0.5)
            new_ob = max(65.0, old_ob - 0.5)
            reasoning = (f"Avg realised return {avg * 100:.2f}% over {n} trades "
                         f"(win rate {wins / len(returns) * 100:.0f}%); relaxing thresholds to capture more.")

    changed = (new_os != old_os) or (new_ob != old_ob)
    if changed:
        params["rsi_oversold_threshold"] = new_os
        params["rsi_overbought_threshold"] = new_ob
        with open(params_path, "w") as f:
            json.dump(params, f, indent=2)
        hyps_path = os.path.join(AGENT_STATE_DIR, "hypotheses.json")
        hyps = _read_json(hyps_path, [])
        hyps.append({
            "timestamp": _now(),
            "reasoning": reasoning,
            "changes": {
                "rsi_oversold_threshold": [old_os, new_os],
                "rsi_overbought_threshold": [old_ob, new_ob],
            },
            "metrics": metrics,
        })
        _write_json(hyps_path, hyps)

    return {
        "changed": changed,
        "reasoning": reasoning,
        "rsi_oversold_threshold": new_os,
        "rsi_overbought_threshold": new_ob,
        "metrics": metrics,
    }


@_tool
def heartbeat() -> dict:
    """Record a liveness marker for the agent path (state/agent/heartbeat.json)."""
    hb = {"status": "ok", "time": _now()}
    _write_json(os.path.join(AGENT_STATE_DIR, "heartbeat.json"), hb)
    return hb


@_tool
def record_buy(symbol: str, price: float, quantity: float, dollars: float = strategy.ORDER_DOLLARS) -> dict:
    """Record a BUY fill placed via the Robinhood MCP so exits and PnL can track it.

    Call this AFTER Robinhood confirms the buy. Stores the entry price the
    stop-loss / take-profit rules will be measured against.
    """
    symbol = symbol.upper().strip()
    positions = _read_json(POSITIONS_PATH, {})
    positions[symbol] = {
        "entry_price": float(price),
        "quantity": float(quantity),
        "invested": float(dollars),
        "timestamp": _now(),
    }
    _write_json(POSITIONS_PATH, positions)
    return {"status": "recorded", "symbol": symbol, "entry_price": price, "quantity": quantity}


@_tool
def record_sell(symbol: str, price: float, reason: str = "MANUAL") -> dict:
    """Record a SELL fill placed via the Robinhood MCP; books realised PnL and closes the position.

    Call this AFTER Robinhood confirms the sell. Uses the recorded entry price to
    compute realised return, then starts the re-entry cooldown. `reason` is free
    text (e.g. STOP_LOSS, TAKE_PROFIT, RSI_OVERBOUGHT, MANUAL).
    """
    symbol = symbol.upper().strip()
    positions = _read_json(POSITIONS_PATH, {})
    entry = positions.get(symbol)

    realised = None
    if entry:
        entry_price = float(entry.get("entry_price", price))
        qty = float(entry.get("quantity", 0) or 0)
        realised = (price - entry_price) / entry_price if entry_price > 0 else 0.0
        records = _read_json(REALIZED_PATH, [])
        records.append({
            "symbol": symbol,
            "entry_price": entry_price,
            "exit_price": float(price),
            "quantity": qty,
            "realised_return": realised,
            "realised_dollars": (price - entry_price) * qty,
            "reason": reason,
            "timestamp": _now(),
        })
        _write_json(REALIZED_PATH, records)
        del positions[symbol]
        _write_json(POSITIONS_PATH, positions)

    _mark_cooldown(symbol)
    return {"status": "closed", "symbol": symbol, "reason": reason, "realised_return": realised}


# --- re-entry cooldown (agent ledger) -----------------------------------------
def _mark_cooldown(symbol):
    cds = _read_json(COOLDOWNS_PATH, {})
    cds[symbol] = _now()
    _write_json(COOLDOWNS_PATH, cds)


def _in_cooldown(symbol, minutes=strategy.REENTRY_COOLDOWN_MINUTES):
    last = _read_json(COOLDOWNS_PATH, {}).get(symbol)
    if not last:
        return False
    try:
        last_t = datetime.datetime.fromisoformat(last)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - last_t).total_seconds() < minutes * 60
    except Exception:
        return False


if __name__ == "__main__":
    transport = os.environ.get("STRATEGY_MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
