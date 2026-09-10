# D.E.B.B.I.E

**Downer's Enhanced Bot Built for Interactive Engagement**

An all-purpose Discord bot with algorithmic trading, Twitch notifications, XP systems, and moderation tools.

---

## Features

- 📋 Paginated command menu via `!!manual`
- 👋 Welcome messages and auto role assignment
- 🎮 Twitch live stream notifications
- 📈 RSI momentum trading via Robinhood (paper + live)
- 💰 PnL reporting with win rate and balance tracking
- 🛡️ Admin commands (ban, kick, timed mute)

## Trading Commands (Owner Only)

| Command                       | Description                            |
| ----------------------------- | -------------------------------------- |
| `!!tickers`                   | View trading universe and RSI settings |
| `!!addticker <TICKER>`        | Add a ticker                           |
| `!!removeticker <TICKER>`     | Remove one or more tickers             |
| `!!positions`                 | View open positions                    |
| `!!pnl [1d\|1w\|1m\|1y\|atd]` | PnL report by time period              |
| `!!setpaperbalance <amount>`  | Set paper trading balance              |

---

## Prerequisites

- Python 3.10+
- Discord bot token
- Robinhood account
- Twitch Developer account

## Installation

```bash
git clone [repository-url]
cd Debbie
pip install discord.py python-dotenv pytz twitchAPI robin_stocks pyotp aiohttp
```

## Configuration

Create a `.env` file:
