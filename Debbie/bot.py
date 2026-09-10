import asyncio
import json
import logging
import os
import random
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import aiohttp
from dotenv import load_dotenv
load_dotenv()

import discord
import pytz
import requests
from discord.ext import commands, tasks
from twitchAPI.twitch import Twitch
import csv


# Get the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))


# ───────────────────────────────
# Config & Globals
# ───────────────────────────────
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

handler = logging.FileHandler(filename='discord.log', encoding='utf8', mode='w')

TOKEN = os.getenv('DISCORD_TOKEN')

guild_streamers = {}
guild_stream_channels = {}

access_token = None
token_expires_at = None
last_status = {}

devRoom = 798914333802496002
debbieDowner = 525885316050190348
recruitRole = 798686544739303436

# Trading Stuff
TRADING_CHANNEL_ID = devRoom
STRATEGY_FILE = Path(script_dir) / "strategy_params.json"
LEDGER_FILE = Path(script_dir) / "trade_ledger.csv"
POSITIONS_FILE = Path(script_dir) / "state/positions.json"

def is_owner():
    async def predicate(ctx):
        return ctx.author.id == debbieDowner
    return commands.check(predicate)

# Twitch API
twitch_client_id = os.getenv("TWITCH_CLIENT_ID")
twitch_client_secret = os.getenv("TWITCH_CLIENT_SECRET")
twitch = Twitch(twitch_client_id, twitch_client_secret)
twitch.authenticate_app
twitchApiEndpoint = "https://api.twitch.tv/helix"

est = pytz.timezone("US/Eastern")

versionUpdate = True
currentVersion = "0.4.5"

prefixes = {}

servers = {
    "waffleHut": 716017804117016607,
    "kso": 1234891495870562365

}

streamers = {
    "waffleHut": ["downabyzmal", "dannyphantym", "wxlfii"],
    "kso": ["jussjalen", "downabyzmal"]
}

streamChannel = {
    "waffleHut": 1385450523712290837,
    "kso": 1423095197499134042
}

last_status = {guild_id: {user: False for user in streamers} for guild_id, streamers in guild_streamers.items()}

# ───────────────────────────────
# Helper Functions
# ───────────────────────────────

bot = commands.Bot(command_prefix="!!", intents=intents, help_command=None)

def createEmbed(title, description, color, footer: str | None = None, fields=None):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text= "D.E.B.B.I.E")
    
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    return embed

async def get_app_access_token():
    """Get and cache an app access token from Twitch."""
    global access_token, token_expires_at

    if access_token and token_expires_at and datetime.now(timezone.utc) < token_expires_at:
        return access_token

    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": twitch_client_id,
        "client_secret": twitch_client_secret,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()
            access_token = data["access_token"]
            token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
            return access_token

APIHeaders = {
    "Client-ID": "d69y0rkoovtt1celx22e957pdcrjhu",
    "Authorization": "Bearer "
}

async def get_user_id(username: str, headers: dict) -> str | None:
    url = "https://api.twitch.tv/helix/users"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"login": username}) as resp:
            if resp.status != 200:
                print(f"Error fetching user ID for {username}: {resp.status}")
                return None
            data = await resp.json()
            if data.get("data"):
                return data["data"][0]["id"]
            return None


async def get_stream_data(user_id: str, headers) -> dict | None:
    url = "https://api.twitch.tv/helix/streams"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"user_id": user_id}) as resp:
            data = await resp.json()
            if data.get("data"):
                stream = data["data"][0]
                return {
                    "title": stream["title"],
                    "game": stream["game_name"],
                    "thumbnail": stream["thumbnail_url"].replace("{width}", "320").replace("{height}", "180"),
                }
            return None


async def check_stream(username: str, headers):
    try:
        user_id = await get_user_id(username, headers)
        if not user_id:
            print(f"User {username} not found.")
            return None
        return await get_stream_data(user_id, headers)
    except Exception as e:
        print(f"Error checking {username}: {e}")
        return None

@tasks.loop(seconds=30)
async def check_streams_loop():
    token = await get_app_access_token()
    headers = {
        "Client-ID": twitch_client_id,
        "Authorization": f"Bearer {token}"
    }

    for server_name, guild_streamers in streamers.items():
        guild_id = servers[server_name]
        channel_id = streamChannel.get(server_name)

        if not channel_id:
            continue

        # Ensure last_status entry exists for this guild
        if guild_id not in last_status:
            last_status[guild_id] = {s: False for s in guild_streamers}

        for username in guild_streamers:
            stream = await check_stream(username, headers)

            was_live = last_status[guild_id].get(username, False)
            is_live = bool(stream)

            if is_live and not was_live:
                embed = discord.Embed(
                    title=f"{username} is LIVE!",
                    description=f"{stream['title']}\n🎮 Playing: {stream['game']}",
                    url=f"https://twitch.tv/{username}",
                    color=discord.Color.purple()
                )
                embed.set_image(url=stream['thumbnail'])

                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(
                        f"@everyone {username} just went live!",
                        embed=embed
                    )

            last_status[guild_id][username] = is_live



# ───────────────────────────────
# Views (UI)
# ───────────────────────────────
class pageView(discord.ui.View):
    def __init__(self, ctx: commands.Context, pages: list[discord.Embed], start_index: int = 0, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.index = max(0, min(start_index, len(pages) - 1))
        self.message: discord.Message | None = None
        if len(self.pages) <= 1:
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.custom_id in {"first","prev","next","last"}:
                    child.disabled = True

    async def _update(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can use these controls.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="first")
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = 0
        await self._update(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        await self._update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await self._update(interaction)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="last")
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = len(self.pages) - 1
        await self._update(interaction)

    @discord.ui.button(label="✖ Close", style=discord.ButtonStyle.danger, custom_id="close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can close this.", ephemeral=True)
            return
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)

# ───────────────────────────────
# Events
# ───────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore command not found errors
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument: {error.param}")
    else:
        print(f"Command error: {error}")
        await ctx.send(f"❌ An error occurred: {str(error)}")

@bot.event
async def on_ready():
    global headers

    if devRoom:
        await bot.get_channel(devRoom).send("D.E.B.B.I.E online!")

    check_streams_loop.start()

    await bot.change_presence(
        activity=discord.Game(name="!!info, !!help"),
        status=discord.Status.online
    )

    print("D.E.B.B.I.E online!")

@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    welcome_channel = bot.get_channel(1386767674096488508)

    if welcome_channel:
        embed = createEmbed(
            title=f"Welcome {member.name}!",
            description=f"Hello {member.mention}, welcome to the server\n Make sure to read the rules and have a great time",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        await welcome_channel.send(embed=embed)

    # Optionally assign a default role
    default_role = discord.utils.get(member.guild.roles, id=798686544739303436)
    if default_role:
        await member.add_roles(default_role)
   
# ───────────────────────────────
# Commands — Information
# ───────────────────────────────

# !!avatar
@bot.command(name="avatar", aliases=["Avatar"])
async def avatar(ctx: commands.Context, member: discord.Member | None = None):
    "Shows a user's avatar."
    target = member or ctx.author
    embed = createEmbed(
        title=f"{target.display_name}'s Avatar",
        description = None,
        color=discord.Color(0x493657)
    )
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)

# !!info
@bot.command(name="info")
async def info(ctx: commands.Context):
    "Bot intro"
    await ctx.send(
        f"**D.E.B.B.I.E (Downer's Enhanced Bot Built for Interactive Engagement**)\n\n"
        f"Good to see you, {ctx.author.mention}! Type `!!help` to for a list of features.\n"
        f"Ping debbie.downer for issues"
    )

# !!help
@bot.command(name="help")
async def manual(ctx: commands.Context):
    """Shows this list of commands"""
    cmds = sorted(bot.commands, key=lambda c: c.name.lower())
    entries = []

    for command in cmds:
        if command.hidden:
            continue
        try:
            if not await command.can_run(ctx):
                continue
        except Exception:
            continue

        usage = f"!!{command.qualified_name}"
        if command.signature:
            usage += f" {command.signature}"
        desc = command.help or "No description provided."
        entries.append((usage, desc))

    per_page = 5
    pages = []
    for i in range(0, len(entries), per_page):
        chunk = entries[i:i + per_page]

        embed = createEmbed(
            title="📘 D.E.B.B.I.E's Commands",
            description="Available commands:",
            color=discord.Color.green()
        )
        for name, desc in chunk:
            embed.add_field(name=name, value=desc, inline=False)
        embed.set_footer(text=f"D.E.B.B.I.E | Page {len(pages)+1}")
        pages.append(embed)

    if not pages:
        await ctx.send("No available commands.")
        return

    view = pageView(ctx, pages)
    view.message = await ctx.send(embed=pages[0], view=view)

    
# !!ping
@bot.command(name="ping", aliases=["Ping"])
async def ping_cmd(ctx: commands.Context):
    "Show bot latency."
    await ctx.send(f"Pong! {round(bot.latency*1000)} ms")

# !!twitchInfo
@bot.command(name="twitchInfo", aliases=["TwitchInfo"])
async def twitchInfo(ctx, username: str):
    """Get basic Twitch info about a user"""
    try:
        # Initialize twitch object
        await twitch.authenticate_app([])

        # Get user info
        user_data = None
        async for user in twitch.get_users(logins=[username]):
            user_data = user
            break
        if not user_data:
            await ctx.send(f"⚠️ No Twitch user found with username: {username}")
            return

        # Get live stream status
        stream_data = None
        async for stream in twitch.get_streams(user_login=[username]):
            stream_data = stream
            break

        # Build response embed
        live_status = (
            f"🟢 **LIVE** — {stream_data.title} playing {stream_data.game_name}"
            if stream_data else
            "🔴 Offline"
        )

        embed = createEmbed(
            title=f"Twitch Info: {user_data.display_name}",
            description=f"{user_data.description or 'No description available.'}\n\n{live_status}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=user_data.profile_image_url)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error fetching Twitch info: {e}")

# ───────────────────────────────
# Commands — Trading
# ───────────────────────────────

# !!tickers
@bot.command(name="tickers")
@is_owner()
async def tickers(ctx):
    """Shows the current trading universe."""
    with open(STRATEGY_FILE) as f:
        params = json.load(f)
    universe = params["universe"]
    ticker_list = ", ".join(universe)
    embed = createEmbed(
        title="📈 Trading Universe",
        description=f"`{ticker_list}`",
        color=discord.Color.blue()
    )
    embed.add_field(name="Total Tickers", value=str(len(universe)), inline=True)
    embed.add_field(name="RSI Buy Below", value=str(params["rsi_oversold_threshold"]), inline=True)
    embed.add_field(name="RSI Sell Above", value=str(params["rsi_overbought_threshold"]), inline=True)
    await ctx.send(embed=embed)

# !!addticker
@bot.command(name="addticker")
@is_owner()
async def addticker(ctx, ticker: str):
    """Adds a ticker to the trading universe."""
    ticker = ticker.upper()
    with open(STRATEGY_FILE) as f:
        params = json.load(f)
    if ticker in params["universe"]:
        await ctx.send(f"💠 `{ticker}` is already in the universe.")
        return
    params["universe"].append(ticker)
    with open(STRATEGY_FILE, "w") as f:
        json.dump(params, f, indent=2)
    await ctx.send(f"💠 `{ticker}` added to the trading universe!")

# !!removeticker
@bot.command(name="removeticker")
@is_owner()
async def removeticker(ctx, ticker: str):
    """Removes a ticker from the trading universe."""
    ticker = ticker.upper()
    with open(STRATEGY_FILE) as f:
        params = json.load(f)
    if ticker not in params["universe"]:
        await ctx.send(f"💠 `{ticker}` is not in the universe.")
        return
    params["universe"].remove(ticker)
    with open(STRATEGY_FILE, "w") as f:
        json.dump(params, f, indent=2)
    await ctx.send(f"💠 `{ticker}` removed from the trading universe.")

# !!positions
@bot.command(name="positions")
@is_owner()
async def positions(ctx):
    """Shows all currently open positions."""
    if not POSITIONS_FILE.exists():
        await ctx.send("💠 No open positions found.")
        return
    with open(POSITIONS_FILE) as f:
        data = json.load(f)
    if not data:
        await ctx.send("💠 No open positions.")
        return

    # Split positions into pages of 10
    items = list(data.items())
    per_page = 10
    pages = []

    for i in range(0, len(items), per_page):
        chunk = items[i:i + per_page]
        embed = createEmbed(
            title="📊 Open Positions",
            description=f"{len(data)} total position(s)",
            color=discord.Color.green()
        )
        for symbol, pos in chunk:
            embed.add_field(
                name=symbol,
                value=f"Entry: ${float(pos['entry_price']):.2f} | Qty: {float(pos['quantity']):.4f} | Invested: ${float(pos['invested']):.2f}",
                inline=False
            )
        embed.set_footer(text=f"D.E.B.B.I.E | Page {len(pages)+1}")
        pages.append(embed)

    view = pageView(ctx, pages)
    view.message = await ctx.send(embed=pages[0], view=view)

# !!pnl
@bot.command(name="pnl")
@is_owner()
async def pnl(ctx, period: str = "1d"):
    """Shows PnL summary. Usage: !!pnl [1d|1w|1m|1y|atd]"""
    if not LEDGER_FILE.exists():
        await ctx.send("💠 No trade ledger found.")
        return

    # Load trades
    trades = []
    with open(LEDGER_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)

    if not trades:
        await ctx.send("💠 No trades recorded yet.")
        return

    # Period filter
    now = datetime.now(timezone.utc)
    period_map = {
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
        "1m": timedelta(days=30),
        "1y": timedelta(days=365),
        "atd": None
    }
    if period not in period_map:
        await ctx.send("⚠️ Invalid period. Use: `1d`, `1w`, `1m`, `1y`, `atd`")
        return

    delta = period_map[period]
    if delta:
        filtered = [
            t for t in trades
            if datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc) >= now - delta
        ]
    else:
        filtered = trades

    # Trades today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trades_today = [
        t for t in trades
        if datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc) >= today_start
    ]
    paper_today = sum(1 for t in trades_today if "SIMULATED" in t["type"])
    live_today = sum(1 for t in trades_today if "LIVE" in t["type"])

    # Match BUY/SELL pairs for win rate and PnL
    open_positions = {}
    wins = 0
    losses = 0
    total_pnl = 0.0

    for t in filtered:
        symbol = t["symbol"]
        action = t["action"]
        price = float(t["price"])

        if action == "BUY":
            open_positions[symbol] = price
        elif action == "SELL" and symbol in open_positions:
            buy_price = open_positions.pop(symbol)
            profit = price - buy_price
            total_pnl += profit
            if profit >= 0:
                wins += 1
            else:
                losses += 1

    total_closed = wins + losses
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    # Robinhood live balance
    rh_balance = 0.0
    try:
        import robinhood as rh
        import json as _json
        result = _json.loads(rh.cmd_balance([]))
        if result.get("status") == "ok":
            rh_balance = result["data"]["equity"]
    except Exception as e:
        print(f"Balance fetch error: {e}")
        rh_balance = 0.0

    # Paper balance
    paper_balance_file = Path(script_dir) / "state/paper_balance.json"
    paper_balance = 0.0
    if paper_balance_file.exists():
        with open(paper_balance_file) as f:
            paper_balance = json.load(f).get("balance", 0.0)

    # Last 5 trades
    last_5 = trades[-5:]
    recent = "\n".join(
        f"`{t['symbol']}` {t['action']} @ ${float(t['price']):.2f} (RSI {float(t['rsi']):.1f}) — {t['type']}"
        for t in last_5
    )

    # Build embed
    period_labels = {
        "1d": "Last 24 Hours",
        "1w": "Last 7 Days",
        "1m": "Last 30 Days",
        "1y": "Last Year",
        "atd": "All Time"
    }

    embed = createEmbed(
        title=f"💰 PnL Report — {period_labels[period]}",
        description=f"**{len(filtered)}** trades in period | **{len(trades_today)}** today",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="💵 Robinhood Balance",
        value=f"${rh_balance:,.2f}",
        inline=True
    )
    embed.add_field(
        name="📝 Paper Balance",
        value=f"${paper_balance:,.2f}",
        inline=True
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(
        name="📈 Simulated PnL",
        value=f"${total_pnl:+,.2f}",
        inline=True
    )
    embed.add_field(
        name="🏆 Win Rate",
        value=f"{win_rate:.1f}% ({wins}W / {losses}L)",
        inline=True
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(
        name="📊 Trades Today",
        value=f"📝 Paper: {paper_today} | 💵 Live: {live_today}",
        inline=False
    )
    embed.add_field(
        name="🕐 Last 5 Trades",
        value=recent or "None yet",
        inline=False
    )

    await ctx.send(embed=embed)

# !!rsi
@bot.command(name="rsi")
@is_owner()
async def rsi_check(ctx, ticker: str):
    """Check the current RSI for a ticker. Usage: !!rsi NVDA"""
    ticker = ticker.upper()
    await ctx.send(f"💠 Fetching RSI for `{ticker}`...")
    
    try:
        import robinhood as rh
        prices = rh.get_historical_close_prices(ticker)
        if not prices or len(prices) < 15:
            await ctx.send(f"⚠️ Not enough price data for `{ticker}`.")
            return

        from strategy import calculate_rsi
        rsi_val = calculate_rsi(prices)
        current_price = prices[-1]

        if rsi_val is None:
            await ctx.send(f"⚠️ Could not calculate RSI for `{ticker}`.")
            return

        with open(STRATEGY_FILE) as f:
            params = json.load(f)
        oversold = params["rsi_oversold_threshold"]
        overbought = params["rsi_overbought_threshold"]

        if rsi_val <= oversold:
            signal = "🟢 BUY SIGNAL"
            color = discord.Color.green()
        elif rsi_val >= overbought:
            signal = "🔴 SELL SIGNAL"
            color = discord.Color.red()
        else:
            signal = "⚪ NEUTRAL"
            color = discord.Color.light_grey()

        embed = createEmbed(
            title=f"📊 RSI Check — {ticker}",
            description=signal,
            color=color
        )
        embed.add_field(name="Current Price", value=f"${current_price:.2f}", inline=True)
        embed.add_field(name="RSI (14)", value=f"{rsi_val:.2f}", inline=True)
        embed.add_field(name="Buy Below", value=str(oversold), inline=True)
        embed.add_field(name="Sell Above", value=str(overbought), inline=True)

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error fetching RSI for `{ticker}`: {e}")


# !!setpaperbalance
@bot.command(name="setpaperbalance")
@is_owner()
async def setpaperbalance(ctx, amount: float):
    """Sets the paper trading starting balance."""
    paper_balance_file = Path(script_dir) / "state/paper_balance.json"
    with open(paper_balance_file, "w") as f:
        json.dump({"balance": amount}, f, indent=2)
    await ctx.send(f"💠 Paper trading balance set to **${amount:,.2f}**")

# ───────────────────────────────
# Commands — Admin
# ───────────────────────────────

# !!ban
@bot.command(name="ban", aliases=["Ban"])
@commands.has_permissions(administrator=True)
async def ban(ctx: commands.Context, member: discord.Member, *, reason=None):
    "Bans a member from the server. Specify a reason if needed."
    await member.ban(reason=reason)
    await ctx.send(f"{member.mention} has been banned from the server.")

# !!kick
@bot.command(name="kick", aliases=["Kick"])
@commands.has_permissions(administrator=True)
async def kick(ctx: commands.Context, member: discord.Member, *, reason=None):
    "Kicks a member from the server. Specify a reason if needed."
    await member.kick(reason=reason)
    await ctx.send(f"{member.mention} has been kicked from the server.")

# !!mute
@bot.command(name="mute", aliases=["Mute"])
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration: int, *, reason=None):
    muted_role = discord.utils.get(ctx.guild.roles, name="syBau")
    if not muted_role:
        await ctx.send("⚠️ 'Muted' role does not exist. Please create it first.")
        return

    await member.add_roles(muted_role, reason=reason)
    await ctx.send(f"🔇 {member.mention} has been muted for {duration} seconds. Reason: {reason or 'No reason provided.'}")

    await asyncio.sleep(duration)
    await member.remove_roles(muted_role)
    await ctx.send(f"🔊 {member.mention} has been unmuted.")


# ───────────────────────────────
# Boot
# ───────────────────────────────
bot.run(TOKEN, log_handler=handler, log_level=logging.DEBUG)
