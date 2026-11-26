
import argparse
import json
import logging

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.reports.report_builder import (
    build_daily_report,
    build_html_report,
)
from src.reports.email_sender import send_report_email


TIMEZONE_NAME = "America/Argentina/Buenos_Aires"

REPORTS_DIR = Path("data/daily_reports")
SENT_DIR = REPORTS_DIR / "sent"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DAILY REPORT] %(levelname)s: %(message)s",
)

logger = logging.getLogger(__name__)


def get_report_date():
    local_now = datetime.now(ZoneInfo(TIMEZONE_NAME))
    return (local_now.date() - timedelta(days=1))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def run_daily_report(report_date, dry_run=False, force=False):
    date_text = report_date.isoformat()

    snapshot_path = REPORTS_DIR / f"{date_text}.json"
    sent_marker_path = SENT_DIR / f"{date_text}.json"

    if sent_marker_path.exists() and not force:
        logger.info(
            "Report for %s was already sent. Skipping.",
            date_text,
        )
        return

    logger.info("Building report for %s", date_text)

    report = build_daily_report(
        report_date=date_text,
        timezone_name=TIMEZONE_NAME,
    )

    html_content = build_html_report(report)

    save_json(snapshot_path, report)

    totals = report["totals"]

    logger.info(
        "Report totals: likes=%s replies=%s reposts=%s "
        "interactions=%s analyzed=%s failed=%s drafted=%s",
        totals["like"],
        totals["reply"],
        totals["repost"],
        totals["interactions"],
        totals["analyzed"],
        totals["failed"],
        totals["drafted"],
    )

    if dry_run:
        html_path = REPORTS_DIR / f"{date_text}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")

        logger.info("Dry run completed. HTML saved to %s", html_path)
        return

    subject = f"Calavera X Agent - Reporte diario {date_text}"

    result = send_report_email(
        subject=subject,
        html_content=html_content,
    )

    # El marcador se crea únicamente después de que SMTP
    # confirma que el mensaje fue aceptado para envío.
    save_json(
        sent_marker_path,
        {
            "date": date_text,
            "sent_at": datetime.now(
                ZoneInfo(TIMEZONE_NAME)
            ).isoformat(),
            "recipients": result["recipients"],
            "subject": result["subject"],
        },
    )

    logger.info("Daily report sent successfully for %s", date_text)


def main():
    parser = argparse.ArgumentParser(
        description="Generate and send the Calavera X Agent daily report."
    )

    parser.add_argument(
        "--date",
        help="Report date in YYYY-MM-DD format. Defaults to yesterday in Argentina.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the report without sending an email.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Send again even if a sent marker already exists.",
    )

    args = parser.parse_args()

    if args.date:
        report_date = datetime.strptime(
            args.date,
            "%Y-%m-%d",
        ).date()
    else:
        report_date = get_report_date()

    run_daily_report(
        report_date=report_date,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
