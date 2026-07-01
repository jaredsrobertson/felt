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
HOLE = "??"   # face-down card


def _card(c) -> str:
    return f"{c.rank}{_SUIT[c.suit]}"


def _hand(cards, hidden: int = 0) -> str:
    shown = [_card(c) for c in cards[: len(cards) - hidden]] + [HOLE] * hidden
    return " ".join(shown)


def _ljust(s: str, w: int) -> str:
    return s + " " * max(0, w - len(s))


def _center(s: str, w: int) -> str:
    pad = max(0, w - len(s))
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


def _table(dealer_line: str, blocks: list[list[str]]) -> str:
    body, width = _columns(blocks)
    header = _center(dealer_line, max(width, len(dealer_line)))
    return f"<pre>{html.escape(header)}\n\n{html.escape(body)}</pre>"


def _bar(remaining: int, total: int, segments: int = 5) -> str:
    """Reverse progress bar of emoji squares; depletes and shifts green->yellow->red."""
    ratio = remaining / total if total > 0 else 0
    filled = max(0, min(segments, round(ratio * segments)))
    fill = "\U0001F7E9" if ratio > 0.5 else "\U0001F7E8" if ratio > 0.25 else "\U0001F7E5"
    return fill * filled + "\u2B1C" * (segments - filled)


# ---- blackjack (lobby = normal text, table = card-glyph columns) -----------
def bj_lobby_text(seated: list[tuple[str, int]], remaining: int, total: int) -> str:
    lines = ["Blackjack \u00b7 place your bets", _bar(remaining, total)]
    if seated:
        lines += [f"{name} \u00b7 {bet}" for name, bet in seated]
    else:
        lines.append("waiting for players\u2026")
    return "\n".join(lines)


def bj_lobby_keys(game_id: str, chips: tuple[int, ...]) -> list[list[ButtonSpec]]:
    return [
        [ButtonSpec(str(c), f"bj:{game_id}:bet:{c}") for c in chips],
        [ButtonSpec("Leave", f"bj:{game_id}:leave")],
    ]


def bj_play_text(game: bj.BlackjackGame, names: dict[str, str]) -> str:
    cur = game.current_player()
    multi = len(game.seats) > 1
    dealer = f"Dealer  {_hand(game.dealer.cards, hidden=1)}"
    blocks = []
    for s in game.seats:
        note = ("BLACKJACK" if s.is_blackjack() else "BUST" if s.is_bust()
                else "stand" if s.stood else f"bet {s.bet}")
        label = names.get(s.player_id, s.player_id)
        if multi and s.player_id == cur:
            label = "> " + label   # active marker (bold can't go inside <pre>)
        blocks.append([f"{_hand(s.cards)} ({s.total()})", label, note])
    return _table(dealer, blocks)


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
    dealer = f"Dealer  {_hand(game.dealer.cards)}  ({game.dealer.total()})"
    blocks = []
    for s in game.seats:
        st = by[s.player_id]
        d = f"+{st.delta}" if st.delta > 0 else str(st.delta)
        blocks.append([f"{_hand(s.cards)} ({s.total()})",
                       names.get(s.player_id, s.player_id), f"{badges[st.outcome.value]} {d}"])
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
