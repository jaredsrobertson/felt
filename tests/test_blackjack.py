import random

from felt.games import blackjack as bj
from felt.games.cards import Card


def _stack(game: bj.BlackjackGame, cards: list[Card]):
    """Force the shoe to deal `cards` from the top (draw pops the end)."""
    game.shoe._cards = list(reversed(cards))


def test_hand_value_and_aces():
    h = bj.Hand("p", 10, [Card("A", "S"), Card("9", "H")])
    assert h.total() == 20 and h.is_soft()
    h.cards.append(Card("5", "D"))  # A+9+5 -> ace demotes -> 15
    assert h.total() == 15 and not h.is_soft()


def test_blackjack_detect():
    h = bj.Hand("p", 10, [Card("A", "S"), Card("K", "H")])
    assert h.is_blackjack() and h.total() == 21


def test_player_win_dealer_bust():
    g = bj.BlackjackGame(rng=random.Random(0))
    # deal order: p,p,dealer,dealer then dealer draws
    _stack(g, [Card("10", "S"), Card("9", "S"),   # player first two
               Card("10", "H"), Card("6", "H"),   # dealer first two (16)
               Card("10", "D")])                  # dealer draws -> 26 bust
    g.add_seat("p", 10)
    g.deal()
    g.action("p", bj.Move.STAND)
    g.dealer_play()
    s = g.settle()[0]
    assert s.outcome is bj.Outcome.WIN and s.delta == 10


def test_player_blackjack_pays_3_2():
    g = bj.BlackjackGame(rng=random.Random(0))
    _stack(g, [Card("A", "S"), Card("10", "H"),   # player A, dealer 10
               Card("K", "S"), Card("7", "H")])   # player K (natural), dealer 7 (17)
    g.add_seat("p", 10)
    g.deal()
    # player auto-done on natural; advance to dealer
    g.dealer_play()
    s = g.settle()[0]
    assert s.outcome is bj.Outcome.BLACKJACK and s.delta == 15


def test_push():
    g = bj.BlackjackGame(rng=random.Random(0))
    _stack(g, [Card("10", "S"), Card("10", "H"),  # player 10, dealer 10
               Card("8", "S"), Card("8", "H")])   # player 18, dealer 18
    g.add_seat("p", 10)
    g.deal()
    g.action("p", bj.Move.STAND)
    g.dealer_play()
    s = g.settle()[0]
    assert s.outcome is bj.Outcome.PUSH and s.delta == 0


def test_double_doubles_bet():
    g = bj.BlackjackGame(rng=random.Random(0))
    _stack(g, [Card("5", "S"), Card("10", "H"),   # player 5, dealer 10
               Card("6", "S"), Card("7", "H"),    # player 11, dealer 17 stand
               Card("9", "D")])                   # player double draw -> 20
    g.add_seat("p", 10)
    g.deal()
    g.action("p", bj.Move.DOUBLE)
    assert g.seats[0].bet == 20
    g.dealer_play()
    s = g.settle()[0]
    assert s.outcome is bj.Outcome.WIN and s.delta == 20


def test_multi_seat_turn_order():
    g = bj.BlackjackGame(rng=random.Random(1))
    g.add_seat("a", 10)
    g.add_seat("b", 10)
    g.deal()
    assert g.current_player() == "a"
    g.action("a", bj.Move.STAND)
    assert g.current_player() == "b"
