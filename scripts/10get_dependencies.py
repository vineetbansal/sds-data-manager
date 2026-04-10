"""aws secretsmanager create-secret --name my-sds-secret --secret-string '{"username":"scott","password":"tiger","host":"localhost","port":5432,"dbname":"sds"}'

Then run this script with SECRET_NAME=my-sds-secret
"""

"""
<parent_src, parent_type, parent_descriptor, child_src, child_type, child_descriptor, relationship_type, DOWNSTREAM

src types = 30 total = spin, repoint + 16 spice kernel names + 12 instrument names from imap_data_access

    ['spin', 'repoint', 'leapseconds', 'planetary_constants', 'imap_frames', 'science_frames', 'spacecraft_clock', 'earth_attitude', 'planetary_ephemeris', 'ephemeris_reconstructed', 'ephemeris_nominal', 'ephemeris_predicted', 'ephemeris_90days', 'ephemeris_long', 'ephemeris_launch', 'attitude_history', 'attitude_predict', 'pointing_attitude', 'glows', 'spacecraft', 'idex', 'hi', 'mag', 'codice', 'ialirt', 'swe', 'lo', 'hit', 'ultra', 'swapi']

type types = 23 total = "best", or (spice, ancillary, spin, repoint) + 18 levels (l0 -> l3e) from imap_data_access

    ['spice', 'ancillary', 'spin', 'repoint', 'l1d', 'l1', 'l2', 'l1b', 'l1c', 'l3d', 'l0', 'l3c', 'l1ca', 'l2b', 'l3', 'l3b', 'l3e', 'l3a', 'l2a', 'l2c', 'l1cb', 'l1a']  # 22 in total

descriptor types = don't know, but "raw" indicates upstream

relationship_type = HARD, HARD_NO_TRIGGER, SOFT_TRIGGER, SOFT_NO_TRIGGER

we form a dict of relationship_type => UPSTREAM: <list_of_3_tuples>, DOWNSTREAM: <list_of_3_tuples>

kickoff_pipeline_jobs only looks at downstream nodes to these "raw" nodes that are HARD/SOFT_TRIGGER
VALID_CADENCE_STRS = ["1mo", "3mo", "6mo", "1yr"]
Code is assuming that a job is a "cadence job" if its downstream of something and:
    data_type in ["l2", "l2b"] and descriptor.split("-")[-1] in cadences

REPOINT_DEPENDENT_INSTRUMENTS = ["glows", "hi", "lo", "ultra"]

"""

import types
from datetime import datetime, timezone

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database.models import (
    RepointFiles,
    SpinFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
    DependencyConfig,
    get_dependencies,
    get_latest_repoint_file,
    get_spin_files,
    verify_science_coverage,
    verify_spin_coverage,
)

# One time setup
# Base.metadata.create_all(db.get_engine())


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

SAMPLE_ROWS = [
    RepointFiles(
        file_path="spice/imap_repoint_20240101_v00.csv",
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        version="00",
        ingestion_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
    ),
    RepointFiles(
        file_path="spice/imap_repoint_20240201_v00.csv",
        end_date=datetime(2024, 2, 29, tzinfo=timezone.utc),
        version="00",
        ingestion_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
    ),
    RepointFiles(
        file_path="spice/imap_repoint_20240301_v01.csv",
        end_date=datetime(2024, 3, 31, tzinfo=timezone.utc),
        version="01",
        ingestion_date=datetime(2024, 4, 1, tzinfo=timezone.utc),
    ),
]


def setup(session):
    for row in SAMPLE_ROWS:
        existing = session.get(RepointFiles, row.file_path)
        if existing is None:
            session.add(row)
    session.commit()


def run_get_latest_repoint_file():
    with db.Session() as session:
        setup(session)

    test_cases = [
        # end_date before the latest file's end_date → should return a filename
        datetime(2024, 3, 15),
        # end_date exactly at the latest file's end_date → should return a filename
        datetime(2024, 3, 31),
        # end_date after the latest file's end_date → should return None
        datetime(2024, 4, 1),
    ]

    print("Calling get_latest_repoint_file for various end_date values:")
    print("-" * 60)
    for end_date in test_cases:
        try:
            result = get_latest_repoint_file(end_date)
            print(f"  end_date={end_date.date()}  →  result={result!r}")
        except ValueError as exc:
            print(f"  end_date={end_date.date()}  →  ValueError: {exc}")


def run_get_dependencies():
    """Demonstrate get_dependencies() for UPSTREAM and DOWNSTREAM lookups."""
    test_cases = [
        # Science node: swe l0 raw → downstream l1a products
        (("swe", "l0", "raw"), "DOWNSTREAM", "HARD"),
        # Science node: swe l1a sci → upstream inputs (HARD + SOFT)
        (("swe", "l1a", "sci"), "UPSTREAM", "ALL"),
        # A node that has no registered dependencies
        (("hit", "l2", "nonexistent-desc"), "DOWNSTREAM", "HARD"),
    ]

    print("Calling get_dependencies for various nodes:")
    print("-" * 60)
    for node, dep_type, relationship in test_cases:
        deps = get_dependencies(node, dep_type, relationship)
        print(f"  node={node}  dep_type={dep_type}  relationship={relationship}")
        if deps:
            for d in deps:
                print(f"    → {d}")
        else:
            print("    → (no dependencies found)")
    print()


def run_dependency_config_methods():
    """Demonstrate DependencyConfig helper methods."""
    config = DependencyConfig()

    # --- kickoff_pipeline_jobs ---
    print("kickoff_pipeline_jobs()  (jobs triggered by any l0/raw file):")
    print("-" * 60)
    jobs = config.kickoff_pipeline_jobs()
    for job in sorted(jobs, key=lambda j: (j["data_source"], j["data_type"])):
        print(f"  {job}")
    print()

    # --- get_all_nodes (DOWNSTREAM only) ---
    print("get_all_nodes(dep_type='DOWNSTREAM')  (sample, first 10):")
    print("-" * 60)
    nodes = config.get_all_nodes(dep_type="DOWNSTREAM")
    for node in sorted(nodes)[:10]:
        print(f"  {node}")
    print(f"  ... ({len(nodes)} total)")
    print()

    # --- get_cadence_jobs ---
    cadences_to_test = [None, "1mo", "1yr"]
    for cadence in cadences_to_test:
        jobs = config.get_cadence_jobs(cadence)
        label = cadence if cadence else "all cadences"
        print(f"get_cadence_jobs(cadence={cadence!r})  [{label}]:")
        print("-" * 60)
        for job in sorted(jobs, key=lambda j: (j["data_source"], j["descriptor"])):
            print(f"  {job}")
        print()


def run_verify_spin_coverage():
    """Demonstrate verify_spin_coverage() with in-memory mock records."""

    def make_spin(start, end):
        return types.SimpleNamespace(
            file_path=f"spin/imap_spin_{start.strftime('%Y%m%d')}.csv",
            start_date=start,
            end_date=end,
        )

    full_coverage = [
        make_spin(datetime(2024, 3, 1), datetime(2024, 3, 15)),
        make_spin(datetime(2024, 3, 15), datetime(2024, 3, 31)),
    ]
    gap_in_middle = [
        make_spin(datetime(2024, 3, 1), datetime(2024, 3, 10)),
        make_spin(datetime(2024, 3, 20), datetime(2024, 3, 31)),  # gap 11–19
    ]
    missing_start = [
        make_spin(datetime(2024, 3, 5), datetime(2024, 3, 31)),  # starts late
    ]

    scenarios = [
        ("Full coverage", full_coverage, datetime(2024, 3, 1), datetime(2024, 3, 31)),
        ("Gap in middle", gap_in_middle, datetime(2024, 3, 1), datetime(2024, 3, 31)),
        ("Missing start", missing_start, datetime(2024, 3, 1), datetime(2024, 3, 31)),
        ("Empty list", [], datetime(2024, 3, 1), datetime(2024, 3, 31)),
    ]

    print("Calling verify_spin_coverage:")
    print("-" * 60)
    for label, records, start, end in scenarios:
        result = verify_spin_coverage(records, start, end)
        print(
            f"  [{label}]  start={start.date()}  end={end.date()}  → covered={result}"
        )
    print()


def run_verify_science_coverage():
    """Demonstrate verify_science_coverage() with in-memory mock records."""

    def make_science(start):
        return types.SimpleNamespace(
            file_path=f"imap_swe_l1a_sci_{start.strftime('%Y%m%d')}_v001.cdf",
            start_date=start,
            version="v001",
            repointing=None,
        )

    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}

    complete = [make_science(datetime(2024, 3, d)) for d in range(1, 6)]  # 1–5
    missing_day = [make_science(datetime(2024, 3, d)) for d in [1, 2, 4, 5]]  # no 3rd
    empty = []

    scenarios = [
        (
            "Complete (5 days)",
            complete,
            datetime(2024, 3, 1),
            datetime(2024, 3, 5),
            None,
        ),
        (
            "Missing day-3",
            missing_day,
            datetime(2024, 3, 1),
            datetime(2024, 3, 5),
            None,
        ),
        ("Empty", empty, datetime(2024, 3, 1), datetime(2024, 3, 5), None),
    ]

    # Repoint-based coverage
    def make_repoint_science(rp):
        return types.SimpleNamespace(
            file_path="imap_hit_l1a_sci_20240301_v001.cdf",
            start_date=datetime(2024, 3, 1),
            version="v001",
            repointing=rp,
        )

    repoint_records = [make_repoint_science(rp) for rp in [1, 2, 3]]
    hit_dep = {"data_source": "hit", "data_type": "l1a", "descriptor": "sci"}
    scenarios += [
        (
            "Repoint complete [1,2,3]",
            repoint_records,
            datetime(2024, 3, 1),
            datetime(2024, 3, 1),
            [1, 2, 3],
        ),
        (
            "Repoint missing 4",
            repoint_records,
            datetime(2024, 3, 1),
            datetime(2024, 3, 1),
            [1, 2, 3, 4],
        ),
    ]

    print("Calling verify_science_coverage:")
    print("-" * 60)
    for label, records, start, end, repoint in scenarios:
        d = hit_dep if repoint is not None else dep
        result = verify_science_coverage(records, start, end, d, repoint)
        rp_str = f"  repoint={repoint}" if repoint is not None else ""
        print(
            f"  [{label}]  start={start.date()}  end={end.date()}{rp_str}  → covered={result}"
        )
    print()


SPIN_SAMPLE_ROWS = [
    SpinFiles(
        file_path="spin/imap_spin_20240101_20240115_v00.csv",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        version="00",
        ingestion_date=datetime(2024, 1, 16, tzinfo=timezone.utc),
    ),
    SpinFiles(
        file_path="spin/imap_spin_20240115_20240131_v00.csv",
        start_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        version="00",
        ingestion_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
    ),
    SpinFiles(
        file_path="spin/imap_spin_20240201_20240229_v01.csv",
        start_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 2, 29, tzinfo=timezone.utc),
        version="01",
        ingestion_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
    ),
]


def setup_spin(session):
    for row in SPIN_SAMPLE_ROWS:
        existing = session.get(SpinFiles, row.file_path)
        if existing is None:
            session.add(row)
    session.commit()


def run_get_spin_files():
    """Demonstrate get_spin_files() with seeded SpinFiles rows."""
    with db.Session() as session:
        setup_spin(session)

    test_cases = [
        # Range fully covered by two Jan files
        (
            datetime(2024, 1, 5, tzinfo=timezone.utc),
            datetime(2024, 1, 20, tzinfo=timezone.utc),
        ),
        # Range spanning both Jan and Feb files
        (
            datetime(2024, 1, 20, tzinfo=timezone.utc),
            datetime(2024, 2, 15, tzinfo=timezone.utc),
        ),
        # Range with no matching files
        (
            datetime(2024, 5, 1, tzinfo=timezone.utc),
            datetime(2024, 5, 31, tzinfo=timezone.utc),
        ),
    ]

    print("Calling get_spin_files for various date ranges:")
    print("-" * 60)
    with db.Session() as session:
        for start, end in test_cases:
            records = get_spin_files(session, start, end)
            print(f"  start={start.date()}  end={end.date()}  → {len(records)} file(s)")
            for r in records:
                print(
                    f"    {r.file_path}  [{r.start_date.date()} – {r.end_date.date()}]  v{r.version}"
                )
    print()


if __name__ == "__main__":
    run_get_latest_repoint_file()
    run_get_dependencies()
    run_dependency_config_methods()
    run_verify_spin_coverage()
    run_verify_science_coverage()
    run_get_spin_files()
