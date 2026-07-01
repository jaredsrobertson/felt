"""Append-only points ledger. Balance is the sum of deltas — never a stored int.

Deposits are idempotent on (kind, ref) so a re-seen Venmo email can't double-credit.
Bets/settles are append rows too, giving a full audit trail.
"""
from __future__ import annotations

import sqlite3


class Ledger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ensure_player(self, player_id: str, handle: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO players(player_id, handle) VALUES (?,?)
               ON CONFLICT(player_id) DO UPDATE SET
                 handle = COALESCE(excluded.handle, players.handle)""",
            (player_id, handle),
        )
        self.conn.commit()

    def player_by_handle(self, handle: str) -> str | None:
        row = self.conn.execute(
            "SELECT player_id FROM players WHERE handle = ? COLLATE NOCASE",
            (handle.lstrip("@"),),
        ).fetchone()
        return row["player_id"] if row else None

    def balance(self, player_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(delta),0) AS bal FROM ledger WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        return int(row["bal"])

    def post(self, player_id: str, delta: int, kind: str, ref: str | None = None) -> bool:
        """Append a ledger row. Returns True if written, False if deduped.

        Idempotent only when ref is provided (deposits). Bets/settles pass ref=None
        and always write.
        """
        try:
            self.conn.execute(
                "INSERT INTO ledger(player_id, delta, kind, ref) VALUES (?,?,?,?)",
                (player_id, delta, kind, ref),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # (kind, ref) already present — replayed event

    def credit_deposit(self, player_id: str, amount: int, message_id: str) -> bool:
        return self.post(player_id, amount, "deposit", ref=message_id)

    def reserve_bet(self, player_id: str, amount: int) -> bool:
        if self.balance(player_id) < amount:
            return False
        return self.post(player_id, -amount, "bet")

    def apply_settle(self, player_id: str, delta: int) -> None:
        self.post(player_id, delta, "settle")
