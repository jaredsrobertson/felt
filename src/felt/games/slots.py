"""Solo slot machine. Pure logic — no telegram, no ledger.

Three reels drawn from a weighted symbol pool (rarer symbols pay more). `evaluate`
returns the GROSS payout for a bet (0 on a loss); the caller has already debited the
stake, so net = payout - bet. Play-money RTP is below 100% (house edge) via the
weights; not precision-tuned (YAGNI).
"""
from __future__ import annotations

import random

# symbol -> weight on each reel (higher = more common)
WEIGHTS = {
    "\U0001F352": 10,  # 🍒 cherry
    "\U0001F34B": 9,   # 🍋 lemon
    "\U0001F349": 7,   # 🍉 watermelon
    "\U0001F514": 5,   # 🔔 bell
    "\u2B50": 3,       # ⭐ star
    "7\uFE0F\u20E3": 2,  # 7️⃣ seven
    "\U0001F48E": 1,   # 💎 diamond
}
SYMBOLS = list(WEIGHTS)
_POOL = [s for s, w in WEIGHTS.items() for _ in range(w)]

# three-of-a-kind gross multiplier on the bet
TRIPLE = {
    "\U0001F48E": 40,  # 💎💎💎
    "7\uFE0F\u20E3": 20,  # 777
    "\u2B50": 12,      # ⭐⭐⭐
    "\U0001F514": 8,   # 🔔🔔🔔
    "\U0001F349": 5,   # 🍉🍉🍉
    "\U0001F34B": 5,   # 🍋🍋🍋
    "\U0001F352": 4,   # 🍒🍒🍒
}
CHERRY = "\U0001F352"


def spin(rng: random.Random | None = None) -> tuple[str, str, str]:
    r = rng or random.Random()
    return (r.choice(_POOL), r.choice(_POOL), r.choice(_POOL))


def evaluate(reels: tuple[str, str, str], bet: int) -> int:
    """Gross payout for `reels` at `bet`. 0 on a loss."""
    a, b, c = reels
    if a == b == c:
        return TRIPLE[a] * bet
    cherries = reels.count(CHERRY)
    if cherries == 2:
        return 2 * bet
    if cherries == 1:
        return 1 * bet      # one cherry returns the stake (a push)
    return 0
