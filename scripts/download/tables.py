"""Download repoint, spin and thruster files covering a date range.

These files are NOT in the SPICE kernels table, so imap_data_access.spice_query()
cannot find them -- they live in their own tables served by the endpoints below
(sds-data-manager: non_spice_table_api.py). sds-data-manager PR #1250 would let
/spice-query reach them via type=repoint/spin/thruster, but it is unmerged, so
this script calls those endpoints directly and hands the file paths to
imap_data_access.download(), which knows where to put them:
    <DATA_DIR>/imap/spice/repoint/     (repoint)
    <DATA_DIR>/imap/spice/spin/        (spin)
    <DATA_DIR>/imap/spice/activities/  (thruster)

Server-side date filtering is not usable here: the endpoints ignore start_date
for the repoint table and their end_date filter is an upper bound only, so this
script filters the returned rows locally. Each table holds a few hundred rows,
so fetching them all is cheap. Their `latest` parameter answers with a 500, so
--latest is applied locally too.

The repoint table has no usable date column either: every row carries the same
end_date, the end of the whole repoint schedule rather than anything about the
file. A repoint file's own date comes from its name instead.

Usage:
    python scripts/download/tables.py 20260501
    python scripts/download/tables.py 20260501 20260601 --latest
    python scripts/download/tables.py 20260501 --types spin thruster
"""

import re
from datetime import datetime
from pathlib import Path

import requests
from _common import (
    auth_headers,
    base_url,
    download_all,
    make_parser,
    parse_dates,
    report_nothing_found,
)

from imap_data_access.file_validation import SPICEFilePath

# Same mapping sds-data-manager PR #1250 adds to /spice-query as `type`
TABLE_ROUTES = {
    "repoint": "/repoint-table",
    "spin": "/spin-table",
    "thruster": "/small-forces-table",
}

# Format of the date fields in the responses
RESPONSE_DATE_FORMAT = "%Y-%m-%d, %H:%M:%S"

# Gives the end_year_doy of imap_<year>_<doy>_<version>.repoint
REPOINT_PATTERN = re.compile(SPICEFilePath.repoint_file_pattern, re.IGNORECASE)


def query_table(file_type):
    """Return every row of the table holding `file_type` files."""
    response = requests.get(
        f"{base_url()}{TABLE_ROUTES[file_type]}",
        headers=auth_headers(),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def coverage(result):
    """Return the (start, end) dates a file covers.

    Spin and thruster files cover an interval and say so in the table. A repoint
    file is a snapshot of the repoint schedule taken on one day, and only its
    name records which day, so its interval is that single day.
    """
    if "start_date" not in result:
        match = REPOINT_PATTERN.match(Path(result["file_path"]).name)
        date = datetime.strptime(match["end_year_doy"], "%Y_%j")
        return date, date
    return (
        datetime.strptime(result["start_date"], RESPONSE_DATE_FORMAT),
        datetime.strptime(result["end_date"], RESPONSE_DATE_FORMAT),
    )


def in_range(result, start_date, end_date):
    """Whether a result's coverage overlaps [start_date, end_date]."""
    result_start, result_end = coverage(result)
    return result_start <= end_date and result_end >= start_date


def keep_latest_versions(results):
    """Keep only the highest-version file for each date range."""
    latest = {}
    for result in results:
        key = coverage(result)
        if key not in latest or result["version"] > latest[key]["version"]:
            latest[key] = result
    return list(latest.values())


def main():
    """Query the non-SPICE tables and download the files in the date range."""
    parser = make_parser(__doc__)
    parser.add_argument(
        "--types",
        nargs="+",
        choices=list(TABLE_ROUTES),
        default=list(TABLE_ROUTES),
        help="File types to download (default: all)",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only download the highest version of each file",
    )
    args = parser.parse_args()
    start_date, end_date = parse_dates(args)

    found = 0
    for file_type in args.types:
        results = [
            result
            for result in query_table(file_type)
            if in_range(result, start_date, end_date)
        ]
        if args.latest:
            results = keep_latest_versions(results)
        if not results:
            continue
        found += len(results)

        print(f"{file_type} ({len(results)})")
        download_all(
            sorted(result["file_path"] for result in results), dry_run=args.dry_run
        )

    if not found:
        report_nothing_found()


if __name__ == "__main__":
    main()
