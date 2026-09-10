# D.E.B.B.I.E — agent notes

Two processes, both run locally:

- `bot.py` — Discord bot. Reads state files, never writes trades.
- `strategy.py` — RSI momentum trader. Owns all trade execution.

Both resolve paths from their own script directory. Never use
CWD-relative paths — the two processes must agree on file locations.

## State

- `strategy_params.json` — RSI thresholds, ticker universe
- `state/strategy.yaml` — stop_loss_pct, take_profit_pct only
- `state/positions.json` — open positions
- `state/paper_balance.json` — paper cash
- `trade_ledger.csv` — full trade history

## Modes

`PAPER_MODE = True` in strategy.py. Set False for live orders.
