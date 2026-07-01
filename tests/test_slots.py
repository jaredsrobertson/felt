import random

from felt.games import slots


def test_spin_returns_three_pool_symbols():
    reels = slots.spin(random.Random(0))
    assert len(reels) == 3
    assert all(sym in slots.SYMBOLS for sym in reels)


def test_triple_pays_multiplier():
    seven = "7\uFE0F\u20E3"
    assert slots.evaluate((seven, seven, seven), 10) == slots.TRIPLE[seven] * 10


def test_two_cherries_pays_double():
    cherry, lemon = slots.CHERRY, "\U0001F34B"
    assert slots.evaluate((cherry, cherry, lemon), 10) == 20


def test_one_cherry_returns_stake():
    cherry, lemon, bell = slots.CHERRY, "\U0001F34B", "\U0001F514"
    assert slots.evaluate((cherry, lemon, bell), 10) == 10


def test_no_match_pays_zero():
    lemon, bell, star = "\U0001F34B", "\U0001F514", "\u2B50"
    assert slots.evaluate((lemon, bell, star), 10) == 0


def test_spin_deterministic_with_seed():
    assert slots.spin(random.Random(42)) == slots.spin(random.Random(42))
