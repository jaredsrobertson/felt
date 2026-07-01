"""Gmail poll -> Venmo parser -> ledger credit.

Called periodically by the bot's JobQueue (see bot/app.py). Opens its own DB
connection so it's safe to run on a worker thread. Idempotent on the Gmail
message id, so re-seeing a mail never double-credits.

Setup (one time):
  1. Google Cloud project, enable the Gmail API.
  2. OAuth client -> credentials.json (path via GMAIL_CREDENTIALS).
  3. First run opens a browser to authorize; token cached to token.json.
     Do this once locally, then ship token.json to the deploy env.

Won't run without those credentials; the bot skips the poll job if they're absent.
"""
from __future__ import annotations

import base64
from pathlib import Path

from ..config import Config
from ..db import connect
from ..ledger import Ledger
from .parser import parse_venmo_email

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
VENMO_QUERY = "from:venmo.com is:unread newer_than:1d"


def _service(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    tok = Path(cfg.gmail_token)
    if tok.exists():
        creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cfg.gmail_credentials, SCOPES)
            creds = flow.run_local_server(port=0)
        tok.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def poll_once(cfg: Config) -> int:
    """One poll cycle. Opens its own DB connection. Returns deposits credited."""
    led = Ledger(connect(cfg.db_path))
    svc = _service(cfg)
    resp = svc.users().messages().list(userId="me", q=VENMO_QUERY).execute()
    credited = 0
    for ref in resp.get("messages", []):
        raw = svc.users().messages().get(userId="me", id=ref["id"], format="raw").execute()
        data = base64.urlsafe_b64decode(raw["raw"].encode("ascii"))
        dep = parse_venmo_email(data)
        if dep is None:
            continue
        # received payment -> credit ONLY if the memo names a known @handle (else
        # it's an unrelated payment; skip). self-deposit -> credit the owner.
        if dep.kind == "received":
            pid = led.player_by_handle(dep.handle) if dep.handle else None
        elif dep.kind == "added" and cfg.owner_id:
            pid = cfg.owner_id
            led.ensure_player(pid)
        else:
            pid = None
        if pid is None:
            continue
        if led.credit_deposit(pid, dep.amount_cents, message_id=ref["id"]):  # 1c = 1pt
            credited += 1
        svc.users().messages().modify(
            userId="me", id=ref["id"], body={"removeLabelIds": ["UNREAD"]}).execute()
    return credited
