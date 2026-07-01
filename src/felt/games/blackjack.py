"""Multi-seat blackjack vs a single bot dealer.

All hands are public (it's everyone-vs-dealer, no hidden cross-player info), so the
bot can render every seat in one group message. No DMs needed.

State machine:
    BETTING -> PLAYER_TURN(seat) -> DEALER_TURN -> SETTLE(done)

Engine is pure: no telegram, no db. Bet amounts are integers (points). Caller is
responsible for reserving points from the ledger before add_seat and for applying
the settle deltas afterward.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from .cards import Card, Shoe


class State(str, Enum):
    BETTING = "betting"
    PLAYER_TURN = "player_turn"
    DEALER_TURN = "dealer_turn"
    DONE = "done"


class Move(str, Enum):
    HIT = "hit"
    STAND = "stand"
    DOUBLE = "double"


class Outcome(str, Enum):
    WIN = "win"
    LOSE = "lose"
    PUSH = "push"
    BLACKJACK = "blackjack"  # natural, pays 3:2


@dataclass
class Hand:
    player_id: str
    bet: int
    cards: list[Card] = field(default_factory=list)
    doubled: bool = False
    stood: bool = False

    def total(self) -> int:
        """Best total <= 21 if possible (aces soft->hard)."""
        total = sum(_rank_value(c.rank) for c in self.cards)
        aces = sum(1 for c in self.cards if c.rank == "A")
        while total > 21 and aces:
            total -= 10  # demote an ace from 11 to 1
            aces -= 1
        return total

    def is_soft(self) -> bool:
        total = sum(_rank_value(c.rank) for c in self.cards)
        aces = sum(1 for c in self.cards if c.rank == "A")
        demotions = 0
        while total > 21 and aces:
            total -= 10
            aces -= 1
            demotions += 1
        return aces - demotions > 0  # an ace still counted as 11

    def is_bust(self) -> bool:
        return self.total() > 21

    def is_blackjack(self) -> bool:
        return len(self.cards) == 2 and self.total() == 21

    def is_done(self) -> bool:
        return self.stood or self.is_bust() or self.is_blackjack()


def _rank_value(rank: str) -> int:
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K", "10"):
        return 10
    return int(rank)


@dataclass
class Settlement:
    player_id: str
    outcome: Outcome
    bet: int
    delta: int  # net change to bankroll (already net of the original bet)


class BlackjackGame:
    def __init__(self, decks: int = 6, dealer_hits_soft_17: bool = False,
                 rng: random.Random | None = None):
        self.shoe = Shoe(decks=decks, rng=rng or random.Random())
        self.dealer_hits_soft_17 = dealer_hits_soft_17
        self.dealer = Hand(player_id="__dealer__", bet=0)
        self.seats: list[Hand] = []
        self.state = State.BETTING
        self._turn = 0  # index into seats

    # ---- setup -------------------------------------------------------------
    def add_seat(self, player_id: str, bet: int) -> None:
        if self.state is not State.BETTING:
            raise RuntimeError("cannot join after deal")
        if any(s.player_id == player_id for s in self.seats):
            raise ValueError("player already seated")
        if bet <= 0:
            raise ValueError("bet must be positive")
        self.seats.append(Hand(player_id=player_id, bet=bet))

    def deal(self) -> None:
        if self.state is not State.BETTING:
            raise RuntimeError("already dealt")
        if not self.seats:
            raise RuntimeError("no players")
        for _ in range(2):
            for s in self.seats:
                s.cards.append(self.shoe.draw())
            self.dealer.cards.append(self.shoe.draw())
        self.state = State.PLAYER_TURN
        self._turn = 0
        self._skip_finished_seats()

    # ---- player actions ----------------------------------------------------
    def current_player(self) -> str | None:
        if self.state is not State.PLAYER_TURN:
            return None
        return self.seats[self._turn].player_id

    def legal_moves(self, player_id: str) -> list[Move]:
        if self.current_player() != player_id:
            return []
        h = self.seats[self._turn]
        moves = [Move.HIT, Move.STAND]
        if len(h.cards) == 2:
            moves.append(Move.DOUBLE)  # caller must verify points for the extra bet
        return moves

    def action(self, player_id: str, move: Move) -> None:
        if self.current_player() != player_id:
            raise RuntimeError("not this player's turn")
        h = self.seats[self._turn]
        if move is Move.HIT:
            h.cards.append(self.shoe.draw())
        elif move is Move.STAND:
            h.stood = True
        elif move is Move.DOUBLE:
            if len(h.cards) != 2:
                raise RuntimeError("can only double on first two cards")
            h.bet *= 2
            h.doubled = True
            h.cards.append(self.shoe.draw())
            h.stood = True
        if h.is_done():
            self._advance()

    def _advance(self) -> None:
        self._turn += 1
        self._skip_finished_seats()  # also flips to DEALER_TURN when seats exhausted

    def _skip_finished_seats(self) -> None:
        while self._turn < len(self.seats) and self.seats[self._turn].is_done():
            self._turn += 1
        if self._turn >= len(self.seats):
            self.state = State.DEALER_TURN

    # ---- dealer + settle ---------------------------------------------------
    def dealer_play(self) -> None:
        if self.state is not State.DEALER_TURN:
            raise RuntimeError("not dealer's turn")
        # If every player busted, dealer need not draw, but we still resolve.
        while True:
            t = self.dealer.total()
            if t < 17 or (t == 17 and self.dealer.is_soft() and self.dealer_hits_soft_17):
                self.dealer.cards.append(self.shoe.draw())
            else:
                break
        self.state = State.DONE

    def settle(self) -> list[Settlement]:
        if self.state is not State.DONE:
            raise RuntimeError("hand not finished")
        out: list[Settlement] = []
        d = self.dealer.total()
        d_bj = self.dealer.is_blackjack()
        d_bust = self.dealer.is_bust()
        for s in self.seats:
            p = s.total()
            if s.is_bust():
                out.append(Settlement(s.player_id, Outcome.LOSE, s.bet, -s.bet))
            elif s.is_blackjack() and not d_bj:
                out.append(Settlement(s.player_id, Outcome.BLACKJACK, s.bet, (3 * s.bet) // 2))
            elif d_bj and not s.is_blackjack():
                out.append(Settlement(s.player_id, Outcome.LOSE, s.bet, -s.bet))
            elif d_bust or p > d:
                out.append(Settlement(s.player_id, Outcome.WIN, s.bet, s.bet))
            elif p < d:
                out.append(Settlement(s.player_id, Outcome.LOSE, s.bet, -s.bet))
            else:
                out.append(Settlement(s.player_id, Outcome.PUSH, s.bet, 0))
        return out
