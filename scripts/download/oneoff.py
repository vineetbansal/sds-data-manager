"""Download named files, or specific products over a date range.

For the one-off pulls that do not justify a full instrument sweep: a handful of
files someone asked about, or a couple of specific (instrument, level,
descriptor) products being watched while a pipeline is under development. Repeat
--product and --file as many times as needed.

Named files are downloaded as given and ignore the date range.

Usage:
    python scripts/download/oneoff.py 20260501 --product lo l1b histrates
    python scripts/download/oneoff.py 20260501 20260601 --product mag l1b burst-magi
    python scripts/download/oneoff.py 20260501 --file imap_mag_l1b_norm-mago_20260410_v001.cdf
"""  # noqa: E501

from _common import (
    DATE_FORMAT,
    download_all,
    make_parser,
    parse_dates,
    report_nothing_found,
)

import imap_data_access


def main():
    """Download the requested products and named files."""
    parser = make_parser(__doc__)
    parser.add_argument(
        "--product",
        nargs=3,
        action="append",
        default=[],
        metavar=("INSTRUMENT", "DATA_LEVEL", "DESCRIPTOR"),
        help="A product to download over the date range; repeatable",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="An exact file name to download; repeatable",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only download the newest version of each file",
    )
    args = parser.parse_args()
    start_date, end_date = parse_dates(args)

    if not args.product and not args.file:
        parser.error("give at least one --product or --file")

    found = 0
    for instrument, data_level, descriptor in args.product:
        results = imap_data_access.query(
            table="science",
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            start_date=start_date.strftime(DATE_FORMAT),
            end_date=end_date.strftime(DATE_FORMAT),
            version="latest" if args.latest else None,
        )
        found += len(results)
        print(f"{instrument} {data_level} {descriptor} ({len(results)})")
        download_all(
            sorted(result["file_path"] for result in results), dry_run=args.dry_run
        )

    if args.product and not found:
        report_nothing_found()

    if args.file:
        print(f"named files ({len(args.file)})")
        download_all(args.file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
