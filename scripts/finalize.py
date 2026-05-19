"""
finalize.py — Monthly Reporter (pure Python, no external API calls)
Called by the Claude Code orchestrator at STEP 3 of the pipeline.

What this script does:
  1. Reads data/ledger.csv (must already exist and be fully populated)
  2. Computes per-month summary: inflow, outflow, net savings, top category, breakdown
  3. Writes data/summary_report.csv with columns:
       month, total_inflow, total_outflow, net_savings, top_category, category_breakdown
  4. Prints the plain-text terminal summary the orchestrator expects:
       Total Inflow:   ₹X
       Total Outflow:  ₹X
       Net Savings:    ₹X
       Top Category:   X (₹X)

Usage:
    python3 scripts/finalize.py
    python3 scripts/finalize.py --ledger data/ledger.csv --out data/summary_report.csv
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR     = PROJECT_ROOT / "data"

DEFAULT_LEDGER = DATA_DIR / "ledger.csv"
DEFAULT_REPORT = DATA_DIR / "summary_report.csv"
ERROR_LOG      = DATA_DIR / "errors.log"


def log_error(message: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] finalize.py: {message}\n")


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        msg = f"ledger.csv not found at {path}. Cannot generate summary."
        log_error(msg)
        print(f"[error] {msg}")
        sys.exit(1)

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        msg = "ledger.csv is empty. Halting — do not generate a partial summary."
        log_error(msg)
        print(f"[error] {msg}")
        sys.exit(1)

    return rows


def compute_monthly_summary(rows: list[dict]) -> list[dict]:
    # Group by YYYY-MM
    months: dict[str, dict] = {}

    for row in rows:
        date     = row["date"]          # YYYY-MM-DD
        month    = date[:7]             # YYYY-MM
        category = row["category"]
        txn_type = row["type"]
        amount   = float(row["amount"])

        if month not in months:
            months[month] = {
                "total_inflow":  0.0,
                "total_outflow": 0.0,
                "category_totals": defaultdict(float),
            }

        if txn_type == "credit":
            months[month]["total_inflow"] += amount
        else:
            months[month]["total_outflow"] += amount
            months[month]["category_totals"][category] += amount

    summary = []
    for month in sorted(months.keys()):
        data           = months[month]
        total_inflow   = round(data["total_inflow"],  2)
        total_outflow  = round(data["total_outflow"], 2)
        net_savings    = round(total_inflow - total_outflow, 2)
        cat_totals     = {k: round(v, 2) for k, v in data["category_totals"].items()}
        top_category   = max(cat_totals, key=cat_totals.get) if cat_totals else "N/A"

        summary.append({
            "month":              month,
            "total_inflow":       total_inflow,
            "total_outflow":      total_outflow,
            "net_savings":        net_savings,
            "top_category":       top_category,
            "category_breakdown": json.dumps(cat_totals, separators=(",", ":")),
        })

    return summary


def write_summary_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "month", "total_inflow", "total_outflow",
        "net_savings", "top_category", "category_breakdown"
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "month":              row["month"],
                "total_inflow":       f"{row['total_inflow']:.2f}",
                "total_outflow":      f"{row['total_outflow']:.2f}",
                "net_savings":        f"{row['net_savings']:.2f}",
                "top_category":       row["top_category"],
                "category_breakdown": row["category_breakdown"],
            })
    print(f"[ok] summary_report.csv written → {out_path}")


def print_orchestrator_summary(rows: list[dict]) -> None:
    for row in rows:
        total_inflow  = float(row["total_inflow"])
        total_outflow = float(row["total_outflow"])
        net_savings   = float(row["net_savings"])
        top_category  = row["top_category"]

        breakdown = json.loads(row["category_breakdown"]) if isinstance(row["category_breakdown"], str) else row["category_breakdown"]
        top_amount = float(breakdown.get(top_category, 0))

        print(f"\n  Period: {row['month']}")
        print(f"  Total Inflow:   ₹{total_inflow:,.2f}")
        print(f"  Total Outflow:  ₹{total_outflow:,.2f}")
        print(f"  Net Savings:    ₹{net_savings:,.2f}")
        print(f"  Top Category:   {top_category} (₹{top_amount:,.2f})")


def main():
    parser = argparse.ArgumentParser(
        description="finalize.py — STEP 3 of the finance pipeline"
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--out",    type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    rows    = read_ledger(args.ledger)
    summary = compute_monthly_summary(rows)
    write_summary_csv(summary, args.out)
    print_orchestrator_summary(summary)
    print("\n[done] Pipeline complete. All outputs written to data/.")


if __name__ == "__main__":
    main()
