"""Telegram bot — the I/O layer. Engines are pure; this wires them to chat.

Run:  python -m felt.bot.app   (needs TELEGRAM_TOKEN; see .env.example)

Blackjack: a self-driving table loop in one text message, edited in place.
    LOBBY (chip buttons + countdown) -> DEAL (auto at 0) -> PLAY (dealer line on
    top, player columns below, per-turn auto-stand) -> SETTLE (cards revealed,
    win/lose + deltas) -> back to LOBBY automatically, carrying bets.
Anyone can join during any betting window. Leave with ✕ or /quit. Everyone-vs-dealer,
all cards public.

Slots: solo, one message — pick a chip, Spin, paytable pays out.

State lives in the engines + ledger; each game tracks its own (chat_id, message_id)
on its state object. JobQueue timers drive the lobby countdown, the per-turn timeout,
and the auto-reopen after settle. If Gmail creds are present, a job polls deposits.

Note: sqlite calls are synchronous inside async handlers. Fine at this scale.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

from ..config import Config
from ..db import connect
from ..games import blackjack as bj
from ..games import slots
from ..ledger import Ledger
from ..venmo.watcher import poll_once
from . import render

log = logging.getLogger("felt")


@dataclass
class Table:
    """Bot-side blackjack table: one message, lobby/play/settle around the engine."""
    chat_id: int
    message_id: int | None = None
    phase: str = "lobby"                                # lobby | playing | settled
    bets: dict[str, int] = field(default_factory=dict)  # player_id -> base bet
    names: dict[str, str] = field(default_factory=dict)  # player_id -> display label
    game: bj.BlackjackGame | None = None
    deadline: float = 0.0


@dataclass
class Slot:
    chat_id: int
    message_id: int | None = None
    bet: int = 5
    reels: tuple | None = None
    last_payout: int | None = None


def _cfg(ctx) -> Config: return ctx.application.bot_data["cfg"]
def _led(ctx) -> Ledger: return ctx.application.bot_data["ledger"]
def _tables(ctx) -> dict: return ctx.application.bot_data["tables"]
def _slots(ctx) -> dict: return ctx.application.bot_data["slots"]


def _kb(rows) -> InlineKeyboardMarkup | None:
    if not rows or not any(rows):
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in rows])


def _disp(user) -> str:
    return f"@{user.username}" if user.username else (user.first_name or str(user.id))


def _seated(t: Table) -> list[tuple[str, int]]:
    return [(t.names.get(pid, pid), bet) for pid, bet in sorted(t.bets.items())]


def _cancel(ctx, name: str) -> None:
    for j in ctx.job_queue.get_jobs_by_name(name):
        j.schedule_removal()


async def _edit(ctx, chat_id, message_id, text, kb=None, parse_mode=None):
    try:
        await ctx.bot.edit_message_text(text, chat_id, message_id,
                                        reply_markup=kb, parse_mode=parse_mode)
    except BadRequest:
        pass  # "message is not modified" on identical countdown ticks, etc.


# ---- generic ---------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    _led(ctx).ensure_player(str(u.id), handle=u.username)
    await update.message.reply_text(
        "Registered. Commands: /bj  /slots  /bank  /quit"
        + ("  /grant" if str(u.id) == _cfg(ctx).owner_id else ""))


async def cmd_bank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bal = _led(ctx).balance(str(update.effective_user.id))
    await update.message.reply_text(f"Balance: {bal} points")


async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg, u = _cfg(ctx), update.effective_user
    uid = str(u.id)
    if not cfg.owner_id:
        await update.message.reply_text(
            f"OWNER_ID not set. Yours is {uid} — add OWNER_ID={uid} to .env and restart.")
        return
    if uid != cfg.owner_id:
        await update.message.reply_text("Not allowed.")
        return
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /grant <amount>")
        return
    led = _led(ctx)
    led.ensure_player(uid, handle=u.username)
    led.post(uid, int(ctx.args[0]), "grant", ref=f"grant-{time.time()}")
    await update.message.reply_text(f"Granted {ctx.args[0]}. Balance: {led.balance(uid)}")


# ---- blackjack: lobby ------------------------------------------------------
async def cmd_bj(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = str(update.effective_chat.id)
    t = _tables(ctx).get(gid)
    if t and t.phase == "lobby":
        await update.message.reply_text("Table's open above — tap a chip.")
        return
    if t and t.phase == "playing":
        await update.message.reply_text("Hand in progress. You'll be able to join the next one.")
        return
    await _open_lobby(update.effective_chat.id, gid, ctx, new_message=True)


async def _open_lobby(chat_id: int, gid: str, ctx, carry: dict[str, int] | None = None,
                      new_message: bool = False):
    cfg, led, tables = _cfg(ctx), _led(ctx), _tables(ctx)
    t = tables.get(gid) or Table(chat_id=chat_id)
    tables[gid] = t
    t.phase, t.game, t.bets = "lobby", None, {}
    for pid, bet in (carry or {}).items():  # auto-loop: re-reserve carried bets
        if led.reserve_bet(pid, bet):
            t.bets[pid] = bet
        else:
            await ctx.bot.send_message(chat_id, f"{t.names.get(pid, pid)} is out of points, sitting out.")
    t.deadline = time.monotonic() + cfg.lobby_seconds
    text = render.bj_lobby_text(_seated(t), cfg.lobby_seconds, cfg.lobby_seconds)
    kb = _kb(render.bj_lobby_keys(gid, cfg.chips))
    if t.message_id and not new_message:
        await _edit(ctx, t.chat_id, t.message_id, text, kb)
    else:
        msg = await ctx.bot.send_message(chat_id, text, reply_markup=kb)
        t.message_id = msg.message_id
    _cancel(ctx, f"tick:{gid}")
    ctx.job_queue.run_repeating(_tick, interval=2, first=2, name=f"tick:{gid}", data=gid)


async def _render_lobby(gid: str, ctx):
    t = _tables(ctx)[gid]
    remaining = max(0, int(t.deadline - time.monotonic()))
    text = render.bj_lobby_text(_seated(t), remaining, _cfg(ctx).lobby_seconds)
    kb = _kb(render.bj_lobby_keys(gid, _cfg(ctx).chips))
    if t.message_id:
        await _edit(ctx, t.chat_id, t.message_id, text, kb)


async def _tick(ctx: ContextTypes.DEFAULT_TYPE):
    gid = ctx.job.data
    t = _tables(ctx).get(gid)
    if t is None or t.phase != "lobby":
        _cancel(ctx, f"tick:{gid}")
        return
    if time.monotonic() >= t.deadline:
        _cancel(ctx, f"tick:{gid}")
        await _deal(gid, ctx)
    else:
        await _render_lobby(gid, ctx)


# ---- blackjack: deal / play ------------------------------------------------
async def _deal(gid: str, ctx):
    t, cfg = _tables(ctx)[gid], _cfg(ctx)
    if not t.bets:
        if t.message_id:
            await _edit(ctx, t.chat_id, t.message_id, "Table closed — no players.")
        _cleanup(gid, ctx)
        return
    t.game = bj.BlackjackGame(decks=cfg.bj_decks, dealer_hits_soft_17=cfg.bj_dealer_hits_soft_17)
    for pid, bet in t.bets.items():
        t.game.add_seat(pid, bet)  # points already reserved in the lobby
    t.game.deal()
    t.phase = "playing"
    if t.game.state is bj.State.DEALER_TURN:  # everyone got a natural
        await _settle(gid, ctx)
        return
    await _render_play(gid, ctx)
    _arm_turn(gid, ctx)


async def _render_play(gid: str, ctx):
    t = _tables(ctx)[gid]
    if t.message_id:
        text = render.bj_play_text(t.game, t.names)
        await _edit(ctx, t.chat_id, t.message_id, text,
                    _kb(render.bj_play_keys(t.game, gid)), parse_mode="HTML")


def _arm_turn(gid: str, ctx):
    _cancel(ctx, f"turn:{gid}")
    ctx.job_queue.run_once(_turn_timeout, _cfg(ctx).turn_seconds, name=f"turn:{gid}", data=gid)


async def _turn_timeout(ctx: ContextTypes.DEFAULT_TYPE):
    gid = ctx.job.data
    t = _tables(ctx).get(gid)
    if not t or t.phase != "playing" or t.game is None:
        return
    cur = t.game.current_player()
    if cur is None:
        return
    t.game.action(cur, bj.Move.STAND)  # AFK -> auto-stand
    if t.game.state is bj.State.DEALER_TURN:
        await _settle(gid, ctx)
    else:
        await _render_play(gid, ctx)
        _arm_turn(gid, ctx)


async def _settle(gid: str, ctx):
    t, led, cfg = _tables(ctx)[gid], _led(ctx), _cfg(ctx)
    _cancel(ctx, f"turn:{gid}")
    t.game.dealer_play()
    settlements = t.game.settle()
    for s in settlements:
        # s.bet == total reserved (doubling already doubled it). delta+bet nets:
        # win -> stake back + winnings; lose -> 0 (stake stays lost); push -> stake back.
        led.apply_settle(s.player_id, s.delta + s.bet)
    t.phase = "settled"  # t.bets still holds base bets -> carried into the next window
    text = render.bj_settle_text(t.game, settlements, t.names)
    kb = _kb([[render.ButtonSpec("\u2715", f"bj:{gid}:quit")]])
    if t.message_id:
        await _edit(ctx, t.chat_id, t.message_id, text, kb, parse_mode="HTML")
    # auto-reopen a fresh betting window (carrying bets) after a short read pause
    _cancel(ctx, f"relobby:{gid}")
    ctx.job_queue.run_once(_relobby, cfg.settle_seconds, name=f"relobby:{gid}", data=gid)


async def _relobby(ctx: ContextTypes.DEFAULT_TYPE):
    gid = ctx.job.data
    t = _tables(ctx).get(gid)
    if t and t.phase == "settled":
        await _open_lobby(t.chat_id, gid, ctx, carry=dict(t.bets))


async def _quit_table(gid: str, ctx):
    t = _tables(ctx).get(gid)
    if not t:
        return
    led = _led(ctx)
    if t.phase == "lobby":
        for pid, bet in list(t.bets.items()):
            led.apply_settle(pid, bet)               # refund lobby reserves
    elif t.phase == "playing" and t.game:
        for s in t.game.seats:
            led.apply_settle(s.player_id, s.bet)     # refund reserved, abandon hand
    if t.message_id:
        await _edit(ctx, t.chat_id, t.message_id, "Table closed.")
    _cleanup(gid, ctx)


def _cleanup(gid: str, ctx):
    for prefix in ("tick", "turn", "relobby"):
        _cancel(ctx, f"{prefix}:{gid}")
    _tables(ctx).pop(gid, None)


async def cmd_quit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = str(update.effective_chat.id)
    if gid in _tables(ctx):
        await _quit_table(gid, ctx)
    elif gid in _slots(ctx):
        s = _slots(ctx).pop(gid)
        if s.message_id:
            await _edit(ctx, s.chat_id, s.message_id, "Slots closed.")
    else:
        await update.message.reply_text("Nothing to quit here.")


# ---- blackjack: callbacks --------------------------------------------------
async def cb_blackjack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    gid, action = parts[1], parts[2]
    t = _tables(ctx).get(gid)
    if t is None:
        await q.answer("Table gone.")
        return
    pid = str(q.from_user.id)
    led = _led(ctx)

    if action == "bet":
        if t.phase != "lobby":
            await q.answer("Betting closed.")
            return
        amount = int(parts[3])
        led.ensure_player(pid, handle=q.from_user.username)
        t.names[pid] = _disp(q.from_user)
        old = t.bets.get(pid, 0)
        if amount > led.balance(pid) + old:
            await q.answer("Not enough points.")
            return
        if old:
            led.apply_settle(pid, old)   # refund prior reserve before re-betting
        led.reserve_bet(pid, amount)
        t.bets[pid] = amount
        await q.answer(f"Bet {amount}")
        await _render_lobby(gid, ctx)

    elif action == "leave":
        if t.phase == "lobby" and pid in t.bets:
            led.apply_settle(pid, t.bets.pop(pid))  # refund
        await q.answer("Left")
        await _render_lobby(gid, ctx)

    elif action in ("hit", "stand", "double"):
        if t.phase != "playing" or t.game is None:
            await q.answer("Not in play.")
            return
        if t.game.current_player() != pid:
            await q.answer("Not your turn.")
            return
        if action == "double":
            seat = next(s for s in t.game.seats if s.player_id == pid)
            if not led.reserve_bet(pid, seat.bet):
                await q.answer("Not enough to double.")
                return
        await q.answer()
        _cancel(ctx, f"turn:{gid}")
        t.game.action(pid, bj.Move(action))
        if t.game.state is bj.State.DEALER_TURN:
            await _settle(gid, ctx)
        else:
            await _render_play(gid, ctx)
            _arm_turn(gid, ctx)

    elif action == "quit":
        await q.answer("Closed")
        await _quit_table(gid, ctx)


# ---- slots -----------------------------------------------------------------
async def cmd_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = str(update.effective_chat.id)
    cfg, led = _cfg(ctx), _led(ctx)
    uid = str(update.effective_user.id)
    led.ensure_player(uid, handle=update.effective_user.username)
    s = Slot(chat_id=update.effective_chat.id, bet=cfg.chips[0])
    _slots(ctx)[gid] = s
    text = render.slots_text(None, s.bet, led.balance(uid), None)
    msg = await ctx.bot.send_message(s.chat_id, text,
                                     reply_markup=_kb(render.slots_keys(gid, cfg.chips)))
    s.message_id = msg.message_id


async def cb_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    gid, action = parts[1], parts[2]
    s = _slots(ctx).get(gid)
    if s is None:
        await q.answer("Machine gone.")
        return
    led = _led(ctx)
    uid = str(q.from_user.id)
    led.ensure_player(uid, handle=q.from_user.username)

    if action == "bet":
        s.bet = int(parts[3])
        await q.answer(f"Bet {s.bet}")
    elif action == "spin":
        if led.balance(uid) < s.bet:
            await q.answer("Not enough points.")
            return
        led.reserve_bet(uid, s.bet)
        s.reels = slots.spin()
        s.last_payout = slots.evaluate(s.reels, s.bet)
        if s.last_payout:
            led.apply_settle(uid, s.last_payout)
        await q.answer()
    elif action == "quit":
        await q.answer("Closed")
        if s.message_id:
            await _edit(ctx, s.chat_id, s.message_id, "Slots closed.")
        _slots(ctx).pop(gid, None)
        return

    text = render.slots_text(s.reels, s.bet, led.balance(uid), s.last_payout)
    await _edit(ctx, s.chat_id, s.message_id, text, _kb(render.slots_keys(gid, _cfg(ctx).chips)))


# ---- deposit poll (JobQueue) -----------------------------------------------
async def _deposit_job(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        n = await asyncio.to_thread(poll_once, _cfg(ctx))
        if n:
            log.info("credited %d deposit(s)", n)
    except Exception:
        log.exception("deposit poll failed")


# ---- bootstrap -------------------------------------------------------------
def build(cfg: Config) -> Application:
    from pathlib import Path
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data.update(cfg=cfg, ledger=Ledger(connect(cfg.db_path)), tables={}, slots={})
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("bj", cmd_bj))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("quit", cmd_quit))
    app.add_handler(CallbackQueryHandler(cb_blackjack, pattern=r"^bj:"))
    app.add_handler(CallbackQueryHandler(cb_slots, pattern=r"^sl:"))
    if Path(cfg.gmail_credentials).exists():
        app.job_queue.run_repeating(_deposit_job, interval=cfg.gmail_poll_seconds, first=10)
    return app


def main():
    from dotenv import load_dotenv
    load_dotenv()                                     # reads .env into os.environ
    asyncio.set_event_loop(asyncio.new_event_loop())  # py3.14: ptb run_polling needs a loop
    logging.basicConfig(level=logging.INFO)
    build(Config.from_env()).run_polling()


if __name__ == "__main__":
    main()
