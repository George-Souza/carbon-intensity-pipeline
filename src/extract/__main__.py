"""CLI for the extraction step.

Examples:
    python -m src.extract --mode backfill
    python -m src.extract --mode backfill --start 2026-08-07 --end 2026-10-05 --force
    python -m src.extract --mode incremental
"""

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta

from src.config import get_settings
from src.db import get_engine
from src.extract.client import CarbonIntensityClient
from src.extract.runner import RunSummary, run_backfill, run_incremental
from src.logging_setup import setup_logging

BACKFILL_DAYS = 60

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.extract",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", required=True, choices=["backfill", "incremental"])
    parser.add_argument("--start", type=date.fromisoformat, help="first day, YYYY-MM-DD (backfill)")
    parser.add_argument("--end", type=date.fromisoformat, help="last day, YYYY-MM-DD (backfill)")
    parser.add_argument("--force", action="store_true", help="re-fetch days already collected (backfill)")
    return parser


def default_window() -> tuple[date, date]:
    """Last 60 days, ending yesterday (UTC)."""
    end = datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=BACKFILL_DAYS - 1)
    return start, end


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.mode == "backfill":
        default_start, default_end = default_window()
        start = args.start or default_start
        end = args.end or default_end
    elif args.start or args.end or args.force:
        build_parser().error("--start, --end and --force only apply to --mode backfill")

    engine = get_engine(settings.database_url)
    try:
        with CarbonIntensityClient(settings) as client:
            if args.mode == "backfill":
                summary = run_backfill(
                    engine,
                    client,
                    start,
                    end,
                    force=args.force,
                    pause_seconds=settings.backfill_pause_seconds,
                )
            else:
                summary = run_incremental(engine, client)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_summary(summary)
    return 0 if summary.status == "success" else 1


def _print_summary(summary: RunSummary) -> None:
    print(
        f"run_id={summary.run_id} status={summary.status} rows_read={summary.rows_read} "
        f"days_ok={summary.days_ok} days_skipped={summary.days_skipped} days_failed={summary.days_failed}"
    )


if __name__ == "__main__":
    sys.exit(main())
