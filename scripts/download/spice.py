"""Download SPICE kernels covering a date range.

Kernels come from the /spice-query endpoint. Its start_time/end_time parameters
are seconds past the J2000 epoch, not the YYYYMMDD dates that
imap_data_access.spice_query() sends (which the endpoint rejects), so this
script talks to the endpoint directly. A kernel matches when its coverage
overlaps the requested range.

Only kernels live in this table. The repoint, spin and thruster files that share
the <DATA_DIR>/imap/spice tree come from separate tables -- use tables.py for
those.

Kernels supersede each other in two ways, and both trim the download a lot:

* A newer version of the same kernel replaces the old one. Handled server side
  by `latest=true`, which is on by default here; pass --all-versions to keep
  every version.
* Predictive kernels are regenerated constantly and overlap heavily, so a newly
  produced file can cover everything several older files covered. --minimal
  drops a kernel when a more recently ingested kernel of the same type already
  covers its whole span of the requested range. Reconstructed kernels that tile
  the range without overlapping (attitude history, for instance) are unaffected.

Usage:
    python scripts/download/spice.py 20260501
    python scripts/download/spice.py 20260501 20260601 --minimal
    python scripts/download/spice.py 20260501 --type ephemeris_predicted metakernel
"""

from collections import defaultdict
from datetime import datetime

import requests
from _common import (
    auth_headers,
    base_url,
    download_all,
    make_parser,
    parse_dates,
    report_nothing_found,
)

# Kernel types in the SPICE table; see _SPICE_DIR_MAPPING in
# imap_data_access/file_validation.py (whose repoint/spin/thruster entries are
# not kernels and are never returned here).
KERNEL_TYPES = [
    "leapseconds",
    "spacecraft_clock",
    "attitude_history",
    "attitude_predict",
    "pointing_attitude",
    "earth_attitude",
    "ephemeris_reconstructed",
    "ephemeris_nominal",
    "ephemeris_predicted",
    "ephemeris_90days",
    "ephemeris_long",
    "ephemeris_launch",
    "planetary_ephemeris",
    "planetary_constants",
    "lagrange_point",
    "imap_frames",
    "science_frames",
    "metakernel",
]

# The epoch that /spice-query's start_time and end_time are measured from
J2000_EPOCH = datetime(2000, 1, 1, 12)

# Format of the coverage dates in the response
RESPONSE_DATE_FORMAT = "%Y-%m-%d, %H:%M:%S"


def to_j2000(date):
    """Seconds from the J2000 epoch to `date`, treated as UTC.

    Leap seconds are ignored, which puts this about a minute off the true
    ephemeris time -- irrelevant when selecting kernels by date.
    """
    return (date - J2000_EPOCH).total_seconds()


def query_kernels(start_date, end_date, kernel_type=None, latest=True):
    """Return the kernels whose coverage overlaps the date range."""
    params = {"start_time": to_j2000(start_date), "end_time": to_j2000(end_date)}
    if kernel_type is not None:
        params["type"] = kernel_type
    if latest:
        params["latest"] = "true"

    response = requests.get(
        f"{base_url()}/spice-query",
        params=params,
        headers=auth_headers(),
        timeout=180,
    )
    response.raise_for_status()
    return response.json()


def keep_minimal_coverage(results, start_date, end_date):
    """Drop kernels whose coverage is already supplied by a newer kernel.

    Kernels are considered newest first (by ingestion date) and one is kept only
    if no kernel already kept, of the same type, spans its whole clipped range.
    """
    kept = []
    covered = defaultdict(list)
    for result in sorted(results, key=lambda result: result["timestamp"], reverse=True):
        result_start = datetime.strptime(
            result["min_date_datetime"], RESPONSE_DATE_FORMAT
        )
        result_end = datetime.strptime(
            result["max_date_datetime"], RESPONSE_DATE_FORMAT
        )
        # Only the part of the kernel inside the requested range matters
        clipped = (max(result_start, start_date), min(result_end, end_date))
        if any(
            earlier[0] <= clipped[0] and clipped[1] <= earlier[1]
            for earlier in covered[result["kernel_type"]]
        ):
            continue
        covered[result["kernel_type"]].append(clipped)
        kept.append(result)
    return kept


def main():
    """Query /spice-query and download the kernels covering the date range."""
    parser = make_parser(__doc__)
    parser.add_argument(
        "--type",
        nargs="+",
        choices=KERNEL_TYPES,
        help="Kernel types to download (default: all of them)",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Keep every version of a kernel, not just the newest",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Skip kernels a more recently ingested kernel already covers",
    )
    args = parser.parse_args()
    start_date, end_date = parse_dates(args)

    results = query_kernels(start_date, end_date, latest=not args.all_versions)
    if args.type:
        results = [result for result in results if result["kernel_type"] in args.type]
    if not results:
        report_nothing_found()
        return

    print(
        f"Found {len(results)} kernel(s) covering {start_date:%Y%m%d}-{end_date:%Y%m%d}"
    )
    if args.minimal:
        superseded = len(results)
        results = keep_minimal_coverage(results, start_date, end_date)
        superseded -= len(results)
        print(f"Skipping {superseded} kernel(s) superseded by a newer one")

    by_type = defaultdict(list)
    for result in results:
        by_type[result["kernel_type"]].append(result["file_name"])

    for kernel_type in sorted(by_type):
        print(f"{kernel_type} ({len(by_type[kernel_type])})")
        download_all(sorted(by_type[kernel_type]), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
