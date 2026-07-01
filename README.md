# felt

A points-based Telegram casino. **Blackjack** (single or multi-seat, everyone vs a
bot dealer) and **Slots** (solo). Bankroll funded by `/grant` (owner) or Venmo
deposit emails — no Venmo API, just inbox parsing.

## How it fits together

```
Venmo email
   -> deposit poll (JobQueue) -> parse + classify -> ledger.credit (idempotent on msg-id)
                                       |
                                Supabase/SQLite (append-only ledger)
                                       |
You <-> Telegram bot <-> game engines (blackjack / slots)
```

One process. The bot polls Telegram for play; if Gmail creds exist, a JobQueue task
polls the inbox for deposits. Both share one DB (the ledger).

## Core idea: state is truth, the message is a view

Nothing important lives in a Telegram message. State lives in the engine + ledger.
Each game tracks its own `(chat_id, message_id)` and edits that one message in place
as state changes. Engines are pure (no telegram, no db) and unit-tested.

## Commands

```
/start    register
/casino   onboarding: how to add chips + play (collapsible, safe to post in-group)
/bank     show chip balance
/cashout  request a payout: /cashout <amount> <@venmo>  or  /cashout <@venmo> (all)
/grant    (owner only) add chips to your balance: /grant <amount>
/bj       open a blackjack table (self-running loop, below)
/slots    open a slot machine (solo)
/quit     close the table / machine in this chat (or tap the X)
```

### Blackjack — the table loop

`/bj` opens a betting window and the table runs itself from there:

1. **Lobby** — tap a chip `[5] [10] [25]` to join (tap again to change bet). A
   countdown ticks; `[Leave]` drops you. Anyone can join during any window.
2. **Deal** — automatic at 0s. The message becomes the table: the dealer on top, each
   hand on its own line (name, cards, total), Hit/Stand/Double for the current seat
   (its name bolded). A per-turn timer auto-stands an AFK seat.
3. **Settle** — every card is revealed (dealer hole included) with WIN / LOSE / PUSH
   and the net chips per player. After a few seconds the window reopens
   automatically, carrying the same bets, so hands run back-to-back.

Stop the loop with the X button or `/quit` (refunds any reserved bet if the hand
hasn't finished). Broke or departed players drop out; an empty table closes itself.

The table is one message, laid out vertically (dealer on top, one hand per line) so
the color suit emoji (♠♥♦♣) render properly — columns aren't used because anything
the client draws as a color emoji is double-width and won't align in a code block.
The active player's name is bolded; face-down is 🎴. The lobby shows a depleting
countdown bar (🟩→🟥) refreshed every 2s. Timings are config: `LOBBY_SECONDS`,
`TURN_SECONDS`, `SETTLE_SECONDS`, plus `CHIPS`.

### Slots

`/slots` opens a machine. Pick a chip, tap Spin: three reels, a paytable pays out
(three-of-a-kind by symbol, two cherries, etc.). Rarer symbols pay more. Solo — your
own balance, one message edited per spin.

## Money / points accounting

Append-only ledger; balance = `SUM(delta)`. Never a stored integer.
- Bet/spin: debited up front (`reserve_bet`).
- Settle: returns winnings (and stake on a win/push) so net matches the result.
- 1 cent deposited = 1 point by default (tune in `watcher.poll_once`).

### Funding your balance

- **`/grant <amount>`** — owner-only, instant, in-chat. The reliable path. If
  `OWNER_ID` is unset, `/grant` replies with your id so you can set it.
- **Venmo deposits** — classified by email subject, with safe routing:
  - A payment from someone is credited only if the memo contains a known
    `@username`. No memo means not credited (an unrelated payment never touches your
    balance).
  - A self-deposit ("you added money to your Venmo balance") is credited to `OWNER_ID`.
  - Anything else is ignored.

### Onboarding & gating

`/casino` posts a collapsible (expandable-blockquote) message anyone can call: it
shows the Venmo deposit target + the memo rule, the game commands, and how to cash
out. Players need chips to play — `/bj` and `/slots` are gated at zero balance, and a
spin/bet/re-bet you can't cover shows an "add funds" nudge instead. Set `VENMO_HANDLE`
so the message and nudges can name your Venmo.

### Cashout

`/cashout <amount> <@venmo>` (or `/cashout <@venmo>` for the whole balance) debits the
chips immediately and DMs the owner the request (user, amount, their Venmo, remaining
balance). Payout is manual — the owner pays on Venmo. If the owner can't be reached the
debit is rolled back. The group only sees a terse confirmation, not the Venmo handle.

  Classification leans on Venmo's subject wording, which varies — the marker lists
  (`RECEIVED_MARKERS`, `ADDED_MARKERS`) are at the top of `venmo/parser.py`; check a
  real email subject and tune them. `/grant` is the bulletproof fallback.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # set TELEGRAM_TOKEN (and OWNER_ID)
python -m felt.bot.app
```

Tests (no token/creds needed):
```bash
pytest -q
```

To auto-load `.env`, either export it (`export $(grep -v '^#' .env | xargs)`) or add
`python-dotenv` and `load_dotenv()` at the top of `main()`.

## Gmail setup (deposit watcher only)

1. Google Cloud project, enable Gmail API.
2. OAuth client (Desktop), download `credentials.json`.
3. First run authorizes in a browser and caches `token.json`. Do it once locally,
   then ship `token.json` to the deploy env. The poll job only registers if
   `credentials.json` is present, so the bot runs fine without any of this.

## Layout

```
src/felt/
  config.py            env config
  db.py                sqlite connection + idempotent schema
  ledger.py            append-only points ledger
  games/
    cards.py           Card, Shoe, render
    blackjack.py       multi-seat engine vs dealer
    slots.py           weighted reels + paytable
  venmo/
    parser.py          .eml -> Deposit (classified received/added)
    watcher.py         Gmail poll -> ledger  (needs creds)
  bot/
    app.py             ptb handlers + the table loop
    render.py          pure state -> text + buttons
tests/                 engines + parser + ledger + slots + money path
```

## Phase next (not built — YAGNI until needed)

- Re-add poker (and the per-player DM surface it needs) as a separate game.
- Split / insurance in blackjack.
- Per-spin slot animation; a Hi-Lo or Roulette table.
- Move the ledger off sync sqlite to async Postgres if concurrency grows.
