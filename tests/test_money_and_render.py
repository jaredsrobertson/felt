"""Headless checks: settle accounting (win pays, loss pays nothing) and that the
text renderer produces the expected lines — no Telegram needed.
"""
import random

from felt.bot import render
from felt.db import connect
from felt.games import blackjack as bj
from felt.games.cards import Card
from felt.ledger import Ledger


def _stack(game, cards):
    game.shoe._cards = list(reversed(cards))


def _settle_one(cards, bet=50):
    """Run a one-player hand on a stacked shoe, mirroring the bot's reserve->settle."""
    led = Ledger(connect(":memory:"))
    led.ensure_player("p")
    led.post("p", 1000, "deposit", ref="seed")
    led.reserve_bet("p", bet)                      # lobby reserve (bot does this)
    g = bj.BlackjackGame(rng=random.Random(0))
    _stack(g, cards)
    g.add_seat("p", bet)
    g.deal()
    if g.current_player() == "p":
        g.action("p", bj.Move.STAND)
    g.dealer_play()
    s = g.settle()[0]
    led.apply_settle("p", s.delta + s.bet)          # bot's settle posting
    return s.outcome, led.balance("p")


def test_win_pays_stake_plus_winnings():
    # player 20, dealer 16 -> draws 10 -> bust. player wins.
    outcome, bal = _settle_one([Card("10", "S"), Card("10", "H"),
                                Card("9", "S"), Card("6", "H"), Card("10", "D")])
    assert outcome is bj.Outcome.WIN
    assert bal == 1050  # started 1000, net +50 on a 50 bet


def test_loss_returns_nothing():
    # player 16 stands, dealer 20. player loses the staked 50, gets no chips back.
    outcome, bal = _settle_one([Card("10", "S"), Card("10", "H"),
                                Card("6", "S"), Card("10", "D")])
    assert outcome is bj.Outcome.LOSE
    assert bal == 950  # started 1000, lost the 50 stake, nothing credited


def test_push_returns_stake():
    outcome, bal = _settle_one([Card("10", "S"), Card("10", "H"),
                                Card("8", "S"), Card("8", "H")])
    assert outcome is bj.Outcome.PUSH
    assert bal == 1000  # stake returned, net zero


def test_play_text_has_dealer_and_player():
    g = bj.BlackjackGame(rng=random.Random(0))
    g.add_seat("1", 50)
    g.deal()
    text = render.bj_play_text(g, {"1": "@jared"}, 20, 25)
    assert "@jared" in text and "(" in text        # player label + total present


def test_settle_text_shows_outcome():
    g = bj.BlackjackGame(rng=random.Random(0))
    g.add_seat("1", 50)
    g.deal()
    while g.current_player():
        g.action("1", bj.Move.STAND)
    g.dealer_play()
    text = render.bj_settle_text(g, g.settle(), {"1": "@jared"})
    assert any(b in text for b in ("WIN", "LOSE", "PUSH", "BLACKJACK"))


def test_casino_text_has_handle_and_memo_rule():
    text = render.casino_text("jared-r", (5, 10, 25))
    assert "@jared-r" in text                      # deposit target shown
    assert "note" in text.lower()                  # memo instruction present
    assert "expandable" in text                    # collapsible in the group
    assert "/bj" in text and "/slots" in text and "/cashout" in text
