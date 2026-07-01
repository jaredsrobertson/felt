from pathlib import Path

from felt.db import connect
from felt.ledger import Ledger
from felt.venmo.parser import parse_venmo_email

FIX = Path(__file__).parent / "fixtures" / "venmo_received.eml"


def test_parse_venmo_email():
    dep = parse_venmo_email(FIX.read_bytes())
    assert dep is not None
    assert dep.kind == "received"
    assert dep.amount_cents == 500
    assert dep.handle == "jared"
    assert dep.code == "bj"
    assert dep.message_id == "abc123.test@venmo.com"


def test_ledger_balance_and_bet():
    led = Ledger(connect(":memory:"))
    led.ensure_player("p1", handle="jared")
    led.post("p1", 1000, "deposit", ref="m1")
    assert led.balance("p1") == 1000
    assert led.reserve_bet("p1", 300) is True
    assert led.balance("p1") == 700
    assert led.reserve_bet("p1", 10_000) is False  # insufficient


def test_deposit_idempotent():
    led = Ledger(connect(":memory:"))
    led.ensure_player("p1")
    assert led.credit_deposit("p1", 500, message_id="gmail-1") is True
    assert led.credit_deposit("p1", 500, message_id="gmail-1") is False  # replay
    assert led.balance("p1") == 500


def test_player_by_handle():
    led = Ledger(connect(":memory:"))
    led.ensure_player("99", handle="Jared")
    assert led.player_by_handle("@jared") == "99"  # case-insensitive, strips @


def _email(subject: str, body: str = "", mid: str = "x@venmo.com") -> bytes:
    return (f"From: Venmo <venmo@venmo.com>\r\nSubject: {subject}\r\n"
            f"Message-ID: <{mid}>\r\nContent-Type: text/plain; charset=\"utf-8\"\r\n\r\n"
            f"{body}\r\n").encode()


def test_self_deposit_classified_added_no_handle():
    dep = parse_venmo_email(_email("You added $20.00 to your Venmo balance",
                                   "Your bank transfer is complete."))
    assert dep is not None
    assert dep.kind == "added"        # watcher routes this to OWNER_ID
    assert dep.amount_cents == 2000
    assert dep.handle is None


def test_unrelated_payment_received_without_handle():
    dep = parse_venmo_email(_email("Alice paid you $15.00", "No memo here."))
    assert dep is not None
    assert dep.kind == "received"
    assert dep.handle is None         # no @handle -> watcher skips (not credited)


def test_unclassifiable_email_returns_none():
    # a venmo email that's neither a payment nor a deposit (e.g. a promo)
    assert parse_venmo_email(_email("Your weekly Venmo summary", "$5.00 in rewards")) is None
