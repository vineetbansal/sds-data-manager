"""Download science data files for a date range.

Queries the science table one instrument at a time so that progress is visible
and a failure part way through is easy to resume. Narrow the download with
--instrument, --data-level and --descriptor; without them this pulls every
science file in the range, which is a lot.

To go straight at named products, give --product instead: each one names an
instrument, a data level and a descriptor, and it can be repeated.

Usage:
    python scripts/download/science.py 20260501 --data-level l0
    python scripts/download/science.py 20260501 20260601 --instrument lo hi
    python scripts/download/science.py 20260501 --descriptor goodtimes --latest
    python scripts/download/science.py 20260501 --product lo l1b histrates
"""

from _common import (
    DATE_FORMAT,
    download_all,
    make_parser,
    parse_dates,
    report_nothing_found,
)

import imap_data_access


def resolve_queries(parser, args):
    """Return the (instrument, data_level, descriptor) triples to query.

    Either the ones named by --product, or one per instrument carrying whatever
    --data-level and --descriptor were given. The two forms would quietly
    contradict each other, so asking for both is an error.
    """
    if not args.product:
        instruments = args.instrument or sorted(imap_data_access.VALID_INSTRUMENTS)
        return [
            (instrument, args.data_level, args.descriptor) for instrument in instruments
        ]

    if args.instrument or args.data_level or args.descriptor:
        parser.error(
            "--product already names an instrument, level and descriptor; drop "
            "--instrument, --data-level and --descriptor"
        )
    for instrument, data_level, _ in args.product:
        if instrument not in imap_data_access.VALID_INSTRUMENTS:
            parser.error(
                f"--product: invalid instrument {instrument!r}, choose from "
                f"{', '.join(sorted(imap_data_access.VALID_INSTRUMENTS))}"
            )
        if data_level not in imap_data_access.VALID_DATALEVELS:
            parser.error(
                f"--product: invalid data level {data_level!r}, choose from "
                f"{', '.join(sorted(imap_data_access.VALID_DATALEVELS))}"
            )
    return [tuple(product) for product in args.product]


def main():
    """Query the science table and download the files in the date range."""
    parser = make_parser(__doc__)
    parser.add_argument(
        "--instrument",
        nargs="+",
        choices=sorted(imap_data_access.VALID_INSTRUMENTS),
        help="Instruments to download (default: all)",
    )
    parser.add_argument(
        "--data-level",
        choices=sorted(imap_data_access.VALID_DATALEVELS),
        help="Data level, e.g. l0 (default: all levels)",
    )
    parser.add_argument("--descriptor", help="Descriptor, e.g. goodtimes")
    parser.add_argument(
        "--product",
        nargs=3,
        action="append",
        default=[],
        metavar=("INSTRUMENT", "DATA_LEVEL", "DESCRIPTOR"),
        help="A single product, e.g. --product lo l1b histrates; repeatable, "
        "and used instead of the three filters above rather than alongside them",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only download the newest version of each file",
    )
    args = parser.parse_args()
    start_date, end_date = parse_dates(args)
    queries = resolve_queries(parser, args)

    found = 0
    for instrument, data_level, descriptor in queries:
        results = imap_data_access.query(
            table="science",
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            start_date=start_date.strftime(DATE_FORMAT),
            end_date=end_date.strftime(DATE_FORMAT),
            version="latest" if args.latest else None,
        )
        if not results:
            continue
        found += len(results)
        label = " ".join(part for part in (instrument, data_level, descriptor) if part)
        print(f"{label} ({len(results)})")
        download_all(
            sorted(result["file_path"] for result in results), dry_run=args.dry_run
        )

    if not found:
        report_nothing_found()


if __name__ == "__main__":
    main()
