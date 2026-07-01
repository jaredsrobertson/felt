"""Parse a Venmo 'you received' email into a structured Deposit.

We never touch a Venmo API. Venmo emails on every received payment; we read the
inbox and parse. Defensive: amount can appear in subject or body, the note carries
the routing info we care about.

Note convention (set by the payer in the Venmo memo):
    "@handle code"   e.g. "@jared bj"  ->  handle=jared, code=bj
The handle maps the Venmo payer to a Telegram player; the code routes to a game.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from html.parser import HTMLParser

_AMOUNT_RE = re.compile(r"\$([0-9]+(?:\.[0-9]{1,2})?)")
_NOTE_RE = re.compile(r"@(\w+)\s+(\w+)")

# Subject markers used to classify an email. Tune these to match the wording your
# Venmo emails actually use (check a real subject line).
RECEIVED_MARKERS = ("paid you", "you received")        # someone paid me
ADDED_MARKERS = ("added", "transfer", "deposit", "to your venmo balance")  # I funded


@dataclass
class Deposit:
    message_id: str
    amount_cents: int
    kind: str              # "received" (someone paid) | "added" (self-deposit)
    actor: str | None      # who paid, per Venmo (display name)
    handle: str | None     # telegram handle parsed from note
    code: str | None       # game code parsed from note
    note: str | None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        s = data.strip()
        if s:
            self.parts.append(s)


def _body_text(msg: Message) -> str:
    chunks: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/html":
            p = _Text()
            p.feed(text)
            text = " ".join(p.parts)
        chunks.append(text)
    return "\n".join(chunks)


def parse_venmo_email(raw: bytes) -> Deposit | None:
    msg = message_from_bytes(raw)
    sender = (msg.get("From") or "").lower()
    subject = msg.get("Subject") or ""
    message_id = (msg.get("Message-ID") or "").strip("<>")

    # Only Venmo mails (received payments AND bank deposits). Tune to your locale.
    if "venmo" not in sender and "venmo" not in subject.lower():
        return None
    body = _body_text(msg)
    haystack = f"{subject}\n{body}"

    m = _AMOUNT_RE.search(subject) or _AMOUNT_RE.search(body)
    if not m:
        return None
    amount_cents = round(float(m.group(1)) * 100)

    # Classify by subject. Received first, so a payment can never be mistaken for a
    # self-deposit and wrongly credited to the owner.
    subj = subject.lower()
    if any(k in subj for k in RECEIVED_MARKERS):
        kind = "received"
    elif any(k in subj for k in ADDED_MARKERS):
        kind = "added"
    else:
        return None  # unclassifiable -> never credited

    # actor: "X paid you" or "you received $.. from X"
    actor = None
    am = re.search(r"from\s+([A-Z][\w .'-]+)", haystack) \
        or re.search(r"([A-Z][\w .'-]+?)\s+paid you", haystack)
    if am:
        actor = am.group(1).strip()

    note = None
    handle = code = None
    nm = _NOTE_RE.search(body)
    if nm:
        handle, code = nm.group(1), nm.group(2)
        note = nm.group(0)

    return Deposit(message_id=message_id, amount_cents=amount_cents, kind=kind,
                   actor=actor, handle=handle, code=code, note=note)
