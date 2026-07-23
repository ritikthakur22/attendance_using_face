"""Export attendance records to CSV, Excel, or both."""

from __future__ import annotations

import argparse
import os
import re
from datetime import date, datetime

import pandas as pd

import database as db

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "attendance_records")
COLUMNS = ["student_id", "name", "class_name", "session_name", "date", "time", "status"]


def valid_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD format") from exc


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return cleaned or "default"


def write_dataframe(df: pd.DataFrame, stem: str, output_format: str) -> list[str]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    paths = []
    if output_format in {"csv", "both"}:
        path = os.path.join(OUTPUT_DIR, f"{stem}.csv")
        df.to_csv(path, index=False)
        paths.append(path)
    if output_format in {"xlsx", "both"}:
        path = os.path.join(OUTPUT_DIR, f"{stem}.xlsx")
        df.to_excel(path, index=False, engine="openpyxl")
        paths.append(path)
    return paths


def export_today_or_date(
    target_date: str | None = None,
    session_name: str | None = None,
    output_format: str = "csv",
) -> list[str]:
    rows = db.get_attendance_by_date(target_date, session_name)
    actual_date = target_date or date.today().isoformat()
    if not rows:
        scope = f" in session {session_name!r}" if session_name else ""
        print(f"No attendance records found for {actual_date}{scope}.")
        return []

    df = pd.DataFrame(rows, columns=COLUMNS)
    suffix = f"_{safe_filename(session_name)}" if session_name else ""
    paths = write_dataframe(df, f"attendance_{actual_date}{suffix}", output_format)
    for path in paths:
        print(f"Exported {len(df)} record(s) to {path}")
    return paths


def export_all(session_name: str | None = None, output_format: str = "csv") -> list[str]:
    rows = db.get_all_attendance(session_name)
    if not rows:
        print("No attendance records found.")
        return []

    df = pd.DataFrame(rows, columns=COLUMNS)
    suffix = f"_{safe_filename(session_name)}" if session_name else ""
    paths = write_dataframe(df, f"attendance_all{suffix}", output_format)

    summary = (
        df.groupby(["student_id", "name", "class_name", "session_name"], dropna=False)
        .agg(days_present=("date", "nunique"), attendance_records=("date", "size"))
        .reset_index()
    )
    summary_paths = write_dataframe(summary, f"attendance_summary{suffix}", output_format)

    for path in paths:
        print(f"Exported {len(df)} record(s) to {path}")
    for path in summary_paths:
        print(f"Exported summary to {path}")
    return paths + summary_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export attendance records")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--date", type=valid_date, help="Specific date in YYYY-MM-DD format")
    selection.add_argument("--all", action="store_true", help="Export every attendance record")
    parser.add_argument("--session", help="Only export one session")
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "both"),
        default="csv",
        help="Output format (default: csv)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db.init_db()
    session_name = args.session.strip() if args.session else None
    if args.session is not None and not session_name:
        print("Error: --session must not be blank.")
        return 2

    if args.all:
        export_all(session_name, args.format)
    else:
        export_today_or_date(args.date, session_name, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
