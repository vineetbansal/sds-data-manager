"""Download ancillary files for a date range.

Ancillary files are per-instrument calibration and configuration products, each
valid over a range of dates rather than describing one. The query matches any
file whose validity overlaps the requested range.

Usage:
    python scripts/download/ancillary.py 20260501
    python scripts/download/ancillary.py 20260501 20260601 --instrument lo
    python scripts/download/ancillary.py 20260501 --descriptor bg-rates-anti-ram
"""

from _common import (
    DATE_FORMAT,
    download_all,
    make_parser,
    parse_dates,
    report_nothing_found,
)

import imap_data_access


def main():
    """Query the ancillary table and download the files in the date range."""
    parser = make_parser(__doc__)
    parser.add_argument(
        "--instrument",
        nargs="+",
        choices=sorted(imap_data_access.VALID_INSTRUMENTS),
        default=sorted(imap_data_access.VALID_INSTRUMENTS),
        help="Instruments to download for (default: all)",
    )
    parser.add_argument(
        "--descriptor", help="Descriptor, e.g. bg-rates-anti-ram-overrides"
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only download the newest version of each file",
    )
    args = parser.parse_args()
    start_date, end_date = parse_dates(args)

    found = 0
    for instrument in args.instrument:
        results = imap_data_access.query(
            table="ancillary",
            instrument=instrument,
            descriptor=args.descriptor,
            start_date=start_date.strftime(DATE_FORMAT),
            end_date=end_date.strftime(DATE_FORMAT),
            version="latest" if args.latest else None,
        )
        if not results:
            continue
        found += len(results)
        print(f"{instrument} ({len(results)})")
        download_all(
            sorted(result["file_path"] for result in results), dry_run=args.dry_run
        )

    if not found:
        report_nothing_found()


if __name__ == "__main__":
    main()
