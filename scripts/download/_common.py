"""Shared helpers for the download scripts in this folder.

Every script here takes a required start date and an optional end date (both
YYYYMMDD, the end date defaulting to today), asks the SDC for the files covering
that range, and hands their paths to imap_data_access.download(), which decides
where each one lands under imap_data_access.config["DATA_DIR"].

Set IMAP_DATA_DIR to control that destination, and IMAP_DATA_ACCESS_URL to point
at a different environment (e.g. https://api.dev.imap-mission.com).
"""

import argparse
from datetime import datetime
from pathlib import Path

import imap_data_access
from imap_data_access.file_validation import generate_imap_file_path

DATE_FORMAT = "%Y%m%d"


def make_parser(description):
    """Build an argument parser with the date range and --dry-run options."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("start_date", help="Start date, YYYYMMDD")
    parser.add_argument(
        "end_date", nargs="?", help="End date, YYYYMMDD (default: today)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be downloaded without downloading it",
    )
    return parser


def parse_dates(args):
    """Return the (start, end) datetimes of the requested range.

    Both are naive UTC; the end date is pushed to the end of its day so that a
    single-day range covers that whole day.
    """
    start = datetime.strptime(args.start_date, DATE_FORMAT)
    end = (
        datetime.strptime(args.end_date, DATE_FORMAT)
        if args.end_date
        else datetime.now()
    )
    return start, end.replace(hour=23, minute=59, second=59)


def base_url():
    """Return the API base URL, with the prefix the configured auth needs.

    imap_data_access.query() does this internally; scripts that build their own
    requests have to do it themselves or they end up on the unauthenticated
    endpoints, which quietly return nothing.
    """
    url = imap_data_access.config["DATA_ACCESS_URL"]
    if imap_data_access.config["API_KEY"] and not url.endswith("/api-key"):
        return f"{url}/api-key"
    if imap_data_access.config["ACCESS_TOKEN"] and not url.endswith("/authorized"):
        return f"{url}/authorized"
    return url


def auth_headers():
    """Headers carrying whichever credential is configured, if any."""
    if imap_data_access.config["API_KEY"]:
        return {"x-api-key": imap_data_access.config["API_KEY"]}
    if imap_data_access.config["ACCESS_TOKEN"]:
        return {"Authorization": f"Bearer {imap_data_access.config['ACCESS_TOKEN']}"}
    return {}


def report_nothing_found():
    """Explain an empty result set, which is easy to mistake for an error."""
    print(
        "No files matched. The archive answers unauthenticated queries with an "
        "empty list rather than an error, so check that IMAP_API_KEY is set if "
        "you expected results."
    )


def local_path(file_path):
    """Where imap_data_access.download() would put `file_path`, or None."""
    try:
        return generate_imap_file_path(Path(file_path).name).construct_path()
    except ValueError:
        return None


def download_all(file_paths, dry_run=False):
    """Download the given file paths, reporting what was already on disk.

    imap_data_access.download() skips files that already exist, so the local
    check here only decides what gets printed.
    """
    already_present = 0
    fetched = 0
    for file_path in file_paths:
        destination = local_path(file_path)
        if destination is not None and destination.exists():
            already_present += 1
            continue
        if dry_run:
            print(f"  would download {destination or file_path}")
        else:
            print(f"  {imap_data_access.download(file_path)}")
        fetched += 1

    print(
        f"  {len(file_paths)} file(s) matched, {already_present} already present, "
        f"{fetched} {'to download' if dry_run else 'downloaded'}"
    )
    return fetched
