"""Card primitives shared by every game. No I/O, no telegram, no deps."""
from __future__ import annotations

import random
from dataclasses import dataclass

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]
SUIT_GLYPH = {"S": "\u2660", "H": "\u2665", "D": "\u2666", "C": "\u2663"}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    def __str__(self) -> str:
        return f"{self.rank}{SUIT_GLYPH[self.suit]}"


def full_deck() -> list[Card]:
    return [Card(r, s) for s in SUITS for r in RANKS]


class Shoe:
    """One or more decks, drawn from the top. Inject rng for deterministic tests."""

    def __init__(self, decks: int = 1, rng: random.Random | None = None):
        self.decks = decks
        self.rng = rng or random.Random()
        self.reshuffle()

    def reshuffle(self) -> None:
        self._cards = full_deck() * self.decks
        self.rng.shuffle(self._cards)

    def draw(self) -> Card:
        if not self._cards:
            self.reshuffle()
        return self._cards.pop()



def render(cards: list[Card], hidden: int = 0) -> str:
    """Render a hand. `hidden` cards from the end show as a back."""
    shown = [str(c) for c in cards[: len(cards) - hidden]]
    shown += ["\U0001F0A0"] * hidden
    return " ".join(shown)
