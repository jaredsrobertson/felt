"""Runtime config from environment. See .env.example."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    telegram_token: str
    db_path: str = "felt.db"
    owner_id: str = ""              # telegram id: owns self-deposits + /grant
    venmo_handle: str = ""          # shown in onboarding/nudges as the deposit target
    bj_decks: int = 6
    bj_dealer_hits_soft_17: bool = False
    lobby_seconds: int = 10         # betting window before auto-deal
    turn_seconds: int = 25          # per-turn auto-stand
    settle_seconds: int = 4         # show results before the next window opens
    chips: tuple[int, ...] = (5, 10, 25)
    # Gmail deposit watcher
    gmail_credentials: str = "credentials.json"
    gmail_token: str = "token.json"
    gmail_poll_seconds: int = 45

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("TELEGRAM_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_TOKEN not set")
        return cls(
            telegram_token=token,
            db_path=os.environ.get("FELT_DB", "felt.db"),
            owner_id=os.environ.get("OWNER_ID", ""),
            venmo_handle=os.environ.get("VENMO_HANDLE", ""),
            bj_decks=int(os.environ.get("BJ_DECKS", "6")),
            bj_dealer_hits_soft_17=os.environ.get("BJ_HIT_SOFT_17", "0") == "1",
            lobby_seconds=int(os.environ.get("LOBBY_SECONDS", "10")),
            turn_seconds=int(os.environ.get("TURN_SECONDS", "25")),
            settle_seconds=int(os.environ.get("SETTLE_SECONDS", "4")),
            chips=tuple(int(c) for c in os.environ.get("CHIPS", "5,10,25").split(",")),
            gmail_credentials=os.environ.get("GMAIL_CREDENTIALS", "credentials.json"),
            gmail_token=os.environ.get("GMAIL_TOKEN", "token.json"),
            gmail_poll_seconds=int(os.environ.get("GMAIL_POLL_SECONDS", "45")),
        )
