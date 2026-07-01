"""Pure rendering: turn game state into (text, button-rows) tuples.

Returns plain data structures (no telegram objects) so render is unit-testable.
The app layer converts ButtonSpec rows into InlineKeyboardMarkup.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from ..games import blackjack as bj
from ..games import slots as sl


@dataclass
class ButtonSpec:
    text: str
    data: str  # callback_data


# Outline (white) suit symbols: U+2664/2661/2662/2667. Unlike the filled U+2660 set,
# these have no emoji presentation, so clients draw them as narrow monochrome text —
# ranks stay full-size and readable, and columns align in a <pre> block.
_SUIT = {"S": "\u2664", "H": "\u2661", "D": "\u2662", "C": "\u2667"}
HOLE = "\u2593]"   # ▓] card back — one shade block + edge (thinner than ▓▓)

# emoji countdown squares are double-width; count them as 2 so centering is exact.
_WIDE = {"\U0001F7E9", "\U0001F7E8", "\U0001F7E5", "\u2B1C"}   # 🟩 🟨 🟥 ⬜


def _dw(s: str) -> int:
    return sum(2 if ch in _WIDE else 1 for ch in s)


def _card(c) -> str:
    return f"{c.rank}{_SUIT[c.suit]}"


def _hand(cards, hidden: int = 0) -> str:
    faces = [f"{_card(c)}]" for c in cards[: len(cards) - hidden]]   # right-edge ]
    faces += [HOLE] * hidden
    return " ".join(faces)


def _ljust(s: str, w: int) -> str:
    return s + " " * max(0, w - len(s))


def _center(s: str, w: int) -> str:
    pad = max(0, w - _dw(s))
    left = pad // 2
    return " " * left + s + " " * (pad - left)


def _columns(blocks: list[list[str]], gutter: int = 4) -> tuple[str, int]:
    widths = [max((len(ln) for ln in col), default=0) for col in blocks]
    nrows = max((len(col) for col in blocks), default=0)
    rows = []
    for r in range(nrows):
        cells = [_ljust(col[r] if r < len(col) else "", w) for col, w in zip(blocks, widths)]
        rows.append((" " * gutter).join(cells).rstrip())
    return "\n".join(rows), max((len(r) for r in rows), default=0)


_SUITS_ROW = "\u2664 \u2661 \u2662 \u2667"   # ♤ ♡ ♢ ♧ (outline = text, not emoji)


def _divider(w: int) -> str:
    mid = f" {_SUITS_ROW} "
    return mid.center(w, "\u2550") if w > len(mid) else mid   # ════ ♤ ♡ ♢ ♧ ════


def _table(dealer_line: str, blocks: list[list[str]], top: str | None = None) -> str:
    body, bw = _columns(blocks)
    w = max(bw, _dw(dealer_line), _dw(top or ""), 24)
    header = _center(dealer_line, w)
    pad = " " * ((w - bw) // 2)                      # center the player block, keep alignment
    body = "\n".join(pad + ln for ln in body.split("\n"))
    lines = [""]                                     # 1 top margin
    if top is not None:
        lines += [_center(top, w), ""]               # countdown centered at top
    lines += [header, "", "", _divider(w), "", body, "", ""]  # 2 below dealer; 2 below body
    return f"<pre>{html.escape('\n'.join(lines))}</pre>"


def _bar(remaining: int, total: int, segments: int = 5) -> str:
    """Reverse progress bar of emoji squares; depletes and shifts green->yellow->red."""
    ratio = remaining / total if total > 0 else 0
    filled = max(0, min(segments, round(ratio * segments)))
    fill = "\U0001F7E9" if ratio > 0.5 else "\U0001F7E8" if ratio > 0.25 else "\U0001F7E5"
    return fill * filled + "\u2B1C" * (segments - filled)


# ---- onboarding ------------------------------------------------------------
def casino_text(venmo_handle: str, chips: tuple[int, ...]) -> str:
    """Public onboarding: collapsed to a title, expands to full instructions."""
    handle = venmo_handle or "the owner"
    if handle != "the owner" and not handle.startswith("@"):
        handle = "@" + handle
    chip_list = "/".join(str(c) for c in chips)
    body = "\n".join([
        f"Add chips: Venmo {html.escape(handle)} and put your Telegram @username in the "
        "payment note — no note means it can't be credited to you. 1\u00a2 = 1 chip, "
        "credited automatically within a minute.",
        "",
        "Games:",
        f"\u2660 /bj \u2014 blackjack vs the dealer. Tap a chip ({chip_list}) to join the "
        "betting window, then Hit / Stand / Double. Everyone plays the same dealer.",
        "\U0001F3B0 /slots \u2014 solo slot machine. Pick a chip, tap Spin.",
        "",
        "Money:",
        "/bank \u2014 your chip balance",
        "/cashout <amount> <@venmo> \u2014 request a payout (owner pays you on Venmo)",
        "",
        "You need chips to play. Out of chips? Just add funds above.",
    ])
    return (f"\U0001F3B0 <b>Welcome to the Casino</b>\n"
            f"<blockquote expandable>{body}</blockquote>")


# ---- blackjack (all messages are monospace <pre> for centering + margins) --
def bj_lobby_text(seated: list[tuple[str, int]], remaining: int, total: int) -> str:
    seats = [f"{name} \u00b7 {bet}" for name, bet in seated]
    w = max([_dw(s) for s in seats] + [_dw(_bar(remaining, total)), _dw("place your bets"), 24])
    lines = ["", _center(_bar(remaining, total), w), "", _center("place your bets", w), ""]
    lines += [_center(s, w) for s in seats] if seats else [_center("waiting for players\u2026", w)]
    lines.append("")
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def bj_lobby_keys(game_id: str, chips: tuple[int, ...]) -> list[list[ButtonSpec]]:
    return [
        [ButtonSpec(str(c), f"bj:{game_id}:bet:{c}") for c in chips],
        [ButtonSpec("Leave", f"bj:{game_id}:leave")],
    ]


def bj_play_text(game: bj.BlackjackGame, names: dict[str, str],
                 remaining: int, total: int) -> str:
    cur = game.current_player()
    multi = len(game.seats) > 1
    up = bj.Hand("", 0, game.dealer.cards[:-1]).total()   # dealer running total (shown cards)
    dealer = f"{_hand(game.dealer.cards, hidden=1)}  ({up})"
    blocks = []
    for s in game.seats:
        note = ("BLACKJACK" if s.is_blackjack() else "BUST" if s.is_bust()
                else "stand" if s.stood else f"bet {s.bet}")
        label = names.get(s.player_id, s.player_id)
        if multi and s.player_id == cur:
            label = "\u00bb " + label
        blocks.append([f"{_hand(s.cards)} ({s.total()})", note, label])   # username on the bottom line
    return _table(dealer, blocks, top=_bar(remaining, total))


def bj_play_keys(game: bj.BlackjackGame, game_id: str) -> list[list[ButtonSpec]]:
    cur = game.current_player()
    row = ([ButtonSpec(m.value.title(), f"bj:{game_id}:{m.value}")
            for m in game.legal_moves(cur)] if cur else [])
    row.append(ButtonSpec("\u2715", f"bj:{game_id}:quit"))
    return [row]


def bj_settle_text(game: bj.BlackjackGame, settlements: list[bj.Settlement],
                   names: dict[str, str]) -> str:
    by = {s.player_id: s for s in settlements}
    badges = {"win": "WIN", "lose": "LOSE", "push": "PUSH", "blackjack": "BLACKJACK"}
    dealer = f"{_hand(game.dealer.cards)}  ({game.dealer.total()})"     # no label
    blocks = []
    for s in game.seats:
        st = by[s.player_id]
        d = f"+{st.delta}" if st.delta > 0 else str(st.delta)
        blocks.append([f"{_hand(s.cards)} ({s.total()})",
                       f"{badges[st.outcome.value]} {d}",
                       names.get(s.player_id, s.player_id)])            # username on the bottom line
    return _table(dealer, blocks)


# ---- slots (solo) ----------------------------------------------------------
def slots_text(reels, bet: int, balance: int, last_payout: int | None) -> str:
    face = "  ".join(reels) if reels else "❔  ❔  ❔"
    lines = ["🎰  Slots", "", f"[ {face} ]", "", f"Bet {bet}   ·   Balance {balance}"]
    if last_payout is not None:
        net = last_payout - bet
        lines.append("")
        lines.append(f"WIN  +{net}" if last_payout > bet
                     else ("Push" if last_payout == bet else f"No win  -{bet}"))
    return "\n".join(lines)


def slots_keys(game_id: str, chips: tuple[int, ...]) -> list[list[ButtonSpec]]:
    return [
        [ButtonSpec(str(c), f"sl:{game_id}:bet:{c}") for c in chips],
        [ButtonSpec("Spin", f"sl:{game_id}:spin"), ButtonSpec("✕", f"sl:{game_id}:quit")],
    ]
