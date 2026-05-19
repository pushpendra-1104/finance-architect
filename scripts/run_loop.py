"""
run_loop.py — CLAUDE CODE NATIVE VERSION
-----------
Stage 2 of the Personal Finance Data Architect pipeline.

DESIGNED FOR CLAUDE CODE — no Anthropic SDK calls anywhere in this file.
Claude Code IS the orchestrator and acts as each sub-agent directly.
This script owns only what Claude Code cannot do cleanly:
  - File I/O (read/write JSON and CSV)
  - The terminal input() prompt
  - Balance arithmetic (exact float math, not LLM math)
  - CSV row building with proper quoting
  - Progress saving and audit log

HOW THIS FITS INTO CLAUDE CODE:

  Claude Code reads CLAUDE.md and drives the pipeline.
  For the confirmation loop, Claude Code does this for each transaction:

    Step 1 — Claude Code reads temp_transactions.json
    Step 2 — Claude Code loads skills/categorization_agent.md as context
    Step 3 — Claude Code reasons about the transaction and picks a category
    Step 4 — Claude Code writes the suggested category into txn["category"]
              and saves temp_transactions.json
    Step 5 — Claude Code calls:
                python scripts/run_loop.py --balance 12500.00
              This script picks up the suggested category, shows it to the
              user, accepts input, computes the balance, and writes the ledger.

  Python handles the deterministic parts.
  Claude Code handles the intelligent parts.
  Neither steps on the other's territory.

USAGE:
  python scripts/run_loop.py --balance 12500.00
  python scripts/run_loop.py --balance 0 --resume    # after interruption

OUTPUT:
  data/temp_transactions.json  — updated in-place after each confirmation
  data/ledger.csv              — one row appended per confirmed transaction
  data/loop_log.json           — full audit trail of every decision
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


# ── Path constants ─────────────────────────────────────────────────────────────

ROOT         = Path(__file__).resolve().parent.parent
DATA_DIR     = ROOT / "data"
SKILLS_DIR   = ROOT / "skills"
TRANSACTIONS = DATA_DIR / "temp_transactions.json"
LEDGER       = DATA_DIR / "ledger.csv"
LOOP_LOG     = DATA_DIR / "loop_log.json"

LEDGER_HEADER = "date,description,category,type,amount,running_balance\n"


# ── File I/O ───────────────────────────────────────────────────────────────────

def load_transactions() -> list[dict]:
    if not TRANSACTIONS.exists():
        print(f"[ERROR] {TRANSACTIONS} not found. Run extract_pdf.py first.", file=sys.stderr)
        sys.exit(1)
    with open(TRANSACTIONS, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or len(data) == 0:
        print("[ERROR] temp_transactions.json is empty or not a JSON array.", file=sys.stderr)
        sys.exit(1)
    return data


def save_transactions(transactions: list[dict]) -> None:
    with open(TRANSACTIONS, "w", encoding="utf-8") as f:
        json.dump(transactions, f, indent=2, ensure_ascii=False)


def ensure_ledger_header() -> None:
    """Write the CSV header row once — only if file is missing or empty."""
    if not LEDGER.exists() or LEDGER.stat().st_size == 0:
        LEDGER.write_text(LEDGER_HEADER, encoding="utf-8")


def get_last_balance_from_ledger() -> float | None:
    """Read running_balance from the last data row of ledger.csv."""
    if not LEDGER.exists():
        return None
    lines = LEDGER.read_text(encoding="utf-8").strip().splitlines()
    data_rows = [l for l in lines[1:] if l.strip()]
    if not data_rows:
        return None
    try:
        return float(data_rows[-1].split(",")[-1])
    except (ValueError, IndexError):
        return None


def get_ledger_row_count() -> int:
    if not LEDGER.exists():
        return 0
    lines = LEDGER.read_text(encoding="utf-8").strip().splitlines()
    return max(0, len(lines) - 1)


def append_to_ledger(csv_row: str) -> None:
    """Append one data row. Never touches the header or existing rows."""
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(csv_row.strip() + "\n")


def load_log() -> list[dict]:
    if LOOP_LOG.exists():
        with open(LOOP_LOG, encoding="utf-8") as f:
            return json.load(f)
    return []


def append_log(log: list[dict], entry: dict) -> None:
    log.append(entry)
    with open(LOOP_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── Balance arithmetic — Python owns this, not Claude Code ────────────────────

def calculate_new_balance(current: float, amount: float, txn_type: str) -> float:
    """
    Exact arithmetic. Always done in Python.
    LLMs are unreliable at float arithmetic — this must never be delegated.
    """
    if txn_type == "credit":
        return round(current + amount, 2)
    return round(current - amount, 2)


# ── CSV row builder — Python owns this too ────────────────────────────────────

def build_csv_row(txn: dict, new_balance: float) -> str:
    """
    RFC 4180 compliant CSV row.
    - Descriptions containing commas are double-quoted.
    - Internal double quotes are escaped as two double quotes.
    - No currency symbols. No comma-formatted numbers.
    """
    desc = txn["description"]
    if '"' in desc:
        desc = desc.replace('"', '""')
    if "," in desc or '"' in desc:
        desc = f'"{desc}"'

    return (
        f"{txn['date']},"
        f"{desc},"
        f"{txn['category']},"
        f"{txn['type']},"
        f"{txn['amount']:.2f},"
        f"{new_balance:.2f}"
    )


# ── Terminal display ───────────────────────────────────────────────────────────

def display_transaction(txn: dict, suggested: str,
                         current_balance: float, idx: int, total: int) -> None:
    direction = "▲ CREDIT" if txn["type"] == "credit" else "▼ DEBIT"
    sign      = "+" if txn["type"] == "credit" else "-"
    width     = 62

    # Wrap long descriptions over two lines
    desc      = txn["description"]
    desc_l1   = desc[:55]
    desc_l2   = desc[55:110] if len(desc) > 55 else ""

    print()
    print("─" * width)
    print(f"  Transaction {idx} of {total}   │   Balance: ₹{current_balance:,.2f}")
    print("─" * width)
    print(f"  Date        {txn['date']}")
    print(f"  Description {desc_l1}")
    if desc_l2:
        print(f"              {desc_l2}")
    print(f"  Amount      {sign}₹{txn['amount']:,.2f}   [{direction}]")
    print(f"  Suggested   {suggested}")
    print("─" * width)


def print_summary(confirmed: int, skipped: int, total: int, balance: float) -> None:
    width = 62
    print()
    print("═" * width)
    print("  LOOP COMPLETE")
    print("═" * width)
    print(f"  Confirmed this session  : {confirmed}")
    print(f"  Already done (skipped)  : {skipped}")
    print(f"  Total transactions      : {total}")
    print(f"  Final running balance   : ₹{balance:,.2f}")
    print(f"  Ledger rows total       : {get_ledger_row_count()}")
    print("═" * width)
    print()
    print("  Next step: python scripts/finalize.py")
    print()


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(starting_balance: float, resume: bool) -> None:

    # ── Load ──────────────────────────────────────────────────────────────────
    print(f"\n[run_loop.py] Loading transactions ...")
    transactions = load_transactions()
    total        = len(transactions)
    log          = load_log()

    print(f"[run_loop.py] {total} transactions found.")

    # ── Resolve balance ───────────────────────────────────────────────────────
    if resume:
        ledger_balance = get_last_balance_from_ledger()
        if ledger_balance is not None:
            current_balance = ledger_balance
            print(f"[run_loop.py] Resuming — last ledger balance: ₹{current_balance:,.2f}")
        else:
            current_balance = starting_balance
            print(f"[run_loop.py] Ledger empty — using --balance: ₹{current_balance:,.2f}")
    else:
        current_balance = starting_balance
        print(f"[run_loop.py] Starting balance: ₹{current_balance:,.2f}")

    ensure_ledger_header()

    # ── Separate pending from done ────────────────────────────────────────────
    pending      = [t for t in transactions if not t.get("confirmed", False)]
    already_done = total - len(pending)

    if already_done:
        print(f"[run_loop.py] {already_done} already confirmed, {len(pending)} remaining.")

    if not pending:
        print("[run_loop.py] All transactions confirmed. Run finalize.py.")
        return

    # ── Instructions ──────────────────────────────────────────────────────────
    print()
    print("  Claude Code has pre-filled a suggested category for each transaction.")
    print("  Press  Enter    → accept the suggestion")
    print("  Type a category → override with your own (Title Case applied automatically)")
    print("  Type  'quit'    → save progress and exit safely")
    print()

    confirmed_count = 0

    # ── Per-transaction loop ──────────────────────────────────────────────────
    for i, txn in enumerate(transactions):

        if txn.get("confirmed", False):
            continue

        global_idx = i + 1

        # Claude Code pre-fills txn["category"] before this script runs.
        # If it's null (e.g. first run without Claude Code), fall back to Other.
        suggested = txn.get("category") or "Other"

        # ── Show card ─────────────────────────────────────────────────────────
        display_transaction(txn, suggested, current_balance, global_idx, total)

        # ── User input ────────────────────────────────────────────────────────
        while True:
            try:
                raw = input("  Category (Enter to confirm, or type override): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  Interrupted — saving progress.")
                save_transactions(transactions)
                sys.exit(0)

            if raw.lower() == "quit":
                print("\n  Saving and exiting ...")
                save_transactions(transactions)
                print(f"  {confirmed_count} confirmed this session.")
                print(f"  Balance so far: ₹{current_balance:,.2f}")
                print("  Resume later:  python scripts/run_loop.py --balance 0 --resume")
                sys.exit(0)

            # Empty input → accept suggestion. Typed input → title-case it.
            final_category = suggested if raw == "" else raw.title()
            break

        # ── Commit ────────────────────────────────────────────────────────────
        txn["category"]  = final_category
        txn["confirmed"] = True

        prev_balance    = current_balance
        current_balance = calculate_new_balance(current_balance, txn["amount"], txn["type"])
        csv_row         = build_csv_row(txn, current_balance)

        append_to_ledger(csv_row)       # write row first
        save_transactions(transactions) # then persist confirmed state

        # ── Confirm ───────────────────────────────────────────────────────────
        sign = "+" if txn["type"] == "credit" else "-"
        print(
            f"  ✓ [{final_category}]   "
            f"₹{prev_balance:,.2f} {sign}₹{txn['amount']:,.2f} "
            f"→ ₹{current_balance:,.2f}"
        )

        confirmed_count += 1

        # ── Audit ─────────────────────────────────────────────────────────────
        append_log(log, {
            "timestamp":          datetime.now().isoformat(),
            "txn_id":             txn["id"],
            "date":               txn["date"],
            "description":        txn["description"],
            "amount":             txn["amount"],
            "type":               txn["type"],
            "suggested_category": suggested,
            "final_category":     final_category,
            "user_overrode":      raw != "",
            "balance_before":     prev_balance,
            "balance_after":      current_balance,
        })

    # ── Done ──────────────────────────────────────────────────────────────────
    print_summary(confirmed_count, already_done, total, current_balance)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="User confirmation loop — runs inside Claude Code pipeline."
    )
    parser.add_argument(
        "--balance",
        type=float,
        required=True,
        help="Starting balance before the first transaction. "
             "Ignored when --resume is used (reads from ledger.csv instead)."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Skip confirmed transactions and read last balance from ledger.csv."
    )
    args = parser.parse_args()
    run(starting_balance=args.balance, resume=args.resume)