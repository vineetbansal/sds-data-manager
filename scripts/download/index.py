"""Index downloaded files into the local SDS database.

Usage:
    python index.py                      # everything on disk
    python index.py imap/lo              # one instrument
    python index.py imap/spice/spin      # one kind of SPICE file
    python index.py --force imap/spice   # recompute SPICE coverage
    python index.py --create-tables      # into an empty database
"""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import imap_data_access
from imap_data_access import SPICEFilePath
from imap_data_access.file_validation import generate_imap_file_path

# The kernels every other kernel's time coverage is computed against.
TIME_KERNEL_TYPES = ("leapseconds", "spacecraft_clock")

# SPICE files that are indexed into their own tables rather than into
# spice_files, each by its own function in spice_indexer.
TABLE_KERNEL_TYPES = ("spin", "repoint", "thruster")


def parse_args():
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="File or directory to index, absolute or relative to the data "
        "directory (default: everything under imap/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be indexed without writing to the database",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index SPICE kernels that already have a row, recomputing "
        "their time coverage. Files in the spin, repoint and thruster tables "
        "are always skipped once present.",
    )
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help="Create any tables that are missing before indexing. Existing "
        "tables and their rows are left alone; nothing is ever dropped.",
    )
    return parser.parse_args()


def load_sds_environment():
    """Make sure DATABASE_URL and S3_BUCKET are set before anything reads them.

    The sds-data-manager repo keeps DATABASE_URL in a .env that `dagster dev`
    loads for you; a plain script gets no such thing, so read it from there.
    """
    import sds_data_manager  # noqa: PLC0415

    if not os.getenv("DATABASE_URL"):
        env_file = Path(sds_data_manager.__file__).parents[1] / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                name, separator, value = line.partition("=")
                if separator and not name.lstrip().startswith("#"):
                    os.environ.setdefault(name.strip(), value.strip())

    if not os.getenv("DATABASE_URL"):
        raise SystemExit(
            "DATABASE_URL is not set and no .env supplied it. Point it at the "
            "local database, e.g. postgresql://scott:tiger@localhost:5432/sds"
        )

    # furnish_best_spice_file refuses to run without this, but only to decide
    # which bucket to download from, and nothing downloads once patched below.
    os.environ.setdefault("S3_BUCKET", "local")


def import_sds():
    """Import the sds-data-manager pieces we index through."""
    try:
        import spiceypy  # noqa: PLC0415

        from sds_data_manager.lambda_code.SDSCode import (  # noqa: PLC0415
            local_indexer,
            spice_utilities,
        )
        from sds_data_manager.lambda_code.SDSCode.database import (  # noqa: PLC0415
            database,
            models,
        )
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (  # noqa: PLC0415
            spice_indexer,
        )
    except ImportError as err:
        raise SystemExit(
            f"Could not import sds_data_manager ({err}). Run this with the "
            "sds-data-manager environment, e.g. "
            "../sds-data-manager/.venv/bin/python scripts/index.py"
        ) from err

    return SimpleNamespace(
        db=database,
        local_indexer=local_indexer,
        models=models,
        spice_indexer=spice_indexer,
        spice_utilities=spice_utilities,
        spiceypy=spiceypy,
    )


def create_tables(sds):
    """Create any missing tables and record the schema version.

    Alembic cannot do this on its own: its root revision starts by altering
    tables that nothing in the chain ever creates, so `alembic upgrade head`
    fails against an empty database. The models are the head schema, indexes
    and all, so build from them and stamp the version afterwards - otherwise
    the next `alembic upgrade` would try to replay the whole chain over a
    schema that already has everything in it.

    create_all only issues CREATE TABLE for tables that are not there, so this
    is safe to pass on a database that is merely missing one of them.
    """
    from sqlalchemy import inspect  # noqa: PLC0415

    engine = sds.db.get_engine()
    before = set(inspect(engine).get_table_names())
    sds.models.Base.metadata.create_all(engine)
    created = sorted(set(inspect(engine).get_table_names()) - before)

    if not created:
        print("schema: every table already present")
        return
    print(f"schema: created {len(created)} table(s): {', '.join(created)}")

    if "alembic_version" not in before:
        print(f"schema: stamped at alembic head {stamp_alembic_head(engine)}")


def stamp_alembic_head(engine):
    """Record the head revision, the way `alembic stamp head` would.

    The revision is read through alembic but written by hand, because
    `alembic.command.stamp` runs env.py, which feeds DATABASE_URL through
    ConfigParser - and a URL containing a % (an encoded password, a search_path
    option) blows up on interpolation there.
    """
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    import sds_data_manager  # noqa: PLC0415

    alembic_ini = Path(sds_data_manager.__file__).parents[1] / "alembic.ini"
    head = ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": head},
        )
    return head


def require_tables(sds):
    """Fail early, and with an explanation, if the schema is not there."""
    from sqlalchemy import inspect  # noqa: PLC0415

    missing = {table.name for table in sds.models.Base.metadata.sorted_tables} - set(
        inspect(sds.db.get_engine()).get_table_names()
    )
    if missing:
        raise SystemExit(
            f"{len(missing)} table(s) missing from the database, including "
            f"{sorted(missing)[0]}. Re-run with --create-tables to build them."
        )


def local_path(s3_key, bucket_name=None):
    """Return the local file an S3 key refers to.

    The key is resolved from its filename rather than its prefix: the SPICE
    indexer is handed keys in two different shapes (see spice_key), and the
    filename alone already determines where the file lives on disk.
    """
    return generate_imap_file_path(os.path.basename(s3_key)).construct_path()


def redirect_s3_to_local(sds):
    """Point the SPICE indexer at the data directory instead of at S3."""
    furnished = {}

    def ingestion_date(s3_key):
        """Local stand-in for the S3 object's LastModified."""
        stat = local_path(s3_key).stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    def furnish_latest_local(kernel_type):
        """Furnish the newest kernel of a type that is actually on disk.

        The real one asks the database for the newest kernel and pulls it from
        S3. Locally the database can name kernels that were never downloaded -
        it is restored from the SDS, the data directory is whatever has been
        downloaded so far - so ask the disk instead.
        """
        if kernel_type not in furnished:
            candidates = sorted(
                path
                for path in (data_dir() / "imap" / "spice").rglob("*")
                if path.is_file() and spice_type(path) == kernel_type
            )
            if not candidates:
                raise FileNotFoundError(f"No {kernel_type} kernel in {data_dir()}")
            # Version is the tail of these filenames, so name order is version
            # order within a type.
            sds.spiceypy.furnsh(str(candidates[-1]))
            furnished[kernel_type] = candidates[-1]
        return furnished[kernel_type]

    # furnish_best_spice_file resolves download_from_s3 through its own module
    # globals, so both copies of that name have to be replaced.
    sds.spice_utilities.download_from_s3 = local_path
    sds.spice_indexer.download_from_s3 = local_path
    sds.spice_indexer.furnish_best_spice_file = furnish_latest_local
    sds.spice_indexer.get_file_ingestion_date = ingestion_date
    # index_spice_file deletes the file it downloaded once it is done with it.
    # That file is now the archive copy, so this must not happen.
    sds.spice_indexer.clear_ephemeral_storage = lambda downloaded_path: None


def data_dir():
    """Return the configured data directory."""
    return Path(imap_data_access.config["DATA_DIR"])


def collect_files(path):
    """Return the files to consider, split into (spice, everything else).

    SPICE files are separated by location rather than by filename, mirroring
    the way S3 routes the imap/spice/ prefix to its own indexer lambda.
    """
    root = Path(path) if path else data_dir() / "imap"
    if not root.is_absolute():
        root = data_dir() / root
    if not root.exists():
        raise SystemExit(f"No such path: {root}")

    paths = (
        [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    )

    # imap/dependency/ holds the dependency sidecars a processing job writes.
    # S3 never routes that prefix to the indexer lambda (see synchronizer's
    # ignore_keys), but the filenames parse as science files, so they would
    # otherwise be inserted into science_files - and their .json extension is
    # not in the extensions enum, which fails the whole run.
    dependency_root = data_dir() / "imap" / "dependency"
    paths = [p for p in paths if dependency_root not in p.parents]

    spice_root = data_dir() / "imap" / "spice"
    spice = [p for p in paths if spice_root in p.parents]
    other = [p for p in paths if spice_root not in p.parents]
    return spice, other


def spice_type(path):
    """Return the SPICE kernel type of a file, or None if it is not one."""
    try:
        file_obj = generate_imap_file_path(path.name)
    except ValueError:
        return None
    if not isinstance(file_obj, SPICEFilePath):
        return None
    return file_obj.spice_metadata["type"]


def spice_key(path, kernel_type):
    """Return the value to store in the file_path column for this file.

    Two conventions are already in this database, both inherited from the
    download script that first populated it: kernels are keyed relative to
    imap/spice, table files by absolute local path. Keep writing both rather
    than adding a third, because repoint_file.py picks the newest repoint by
    ordering on file_path, and a mixed set of prefixes would order by prefix.
    """
    if kernel_type in TABLE_KERNEL_TYPES:
        return str(path)
    return path.relative_to(data_dir() / "imap" / "spice").as_posix()


def indexed_basenames(sds, model, column="file_path"):
    """Return the filenames already present in a table."""
    with sds.db.Session() as session:
        rows = session.query(getattr(model, column)).all()
    return {os.path.basename(row[0]) for row in rows}


def index_regular_files(sds, paths, dry_run):
    """Index science, quicklook, release and ancillary files.

    local_indexer.index_file skips anything already in its table and anything
    it does not recognise, so the whole list can be handed to it.
    """
    indexed = []
    with sds.db.Session() as session:
        for path in paths:
            if sds.local_indexer.index_file(path, session):
                indexed.append(path)
                print(f"  {path.name}")
        if dry_run:
            session.rollback()
        else:
            session.commit()
    return indexed


def index_spice_files(sds, paths, dry_run, force):
    """Index SPICE kernels and the spin, repoint and thruster tables."""
    already = {
        "spin": indexed_basenames(sds, sds.models.SpinFiles),
        "repoint": indexed_basenames(sds, sds.models.RepointFiles),
        "thruster": indexed_basenames(sds, sds.models.SmallForcesFile),
        None: indexed_basenames(sds, sds.models.SPICEFiles, "file_name"),
    }

    def pending(path):
        """Whether this file still needs indexing."""
        kernel_type = spice_type(path)
        if kernel_type is None:
            return False
        table = kernel_type if kernel_type in TABLE_KERNEL_TYPES else None
        if path.name not in already[table]:
            return True
        # Only spice_files can be rewritten; the other three insert plainly and
        # would just raise on the duplicate.
        return force and table is None

    # Time kernels first: every other kernel's coverage is computed against the
    # newest leapsecond and clock kernels, so a newly arrived one should be in
    # place before the kernels whose rows are dated with it.
    todo = sorted(
        (path for path in paths if pending(path)),
        key=lambda path: (spice_type(path) not in TIME_KERNEL_TYPES, path.name),
    )

    indexed = []
    failed = []
    for path in todo:
        kernel_type = spice_type(path)
        key = spice_key(path, kernel_type)
        if dry_run:
            print(f"  {path.name}")
            indexed.append(path)
            continue
        try:
            if kernel_type == "spin":
                sds.spice_indexer.index_spin_file(key)
            elif kernel_type == "repoint":
                # The repoint row's end date is read back out of the pointing
                # table, so the pointing data has to go in first.
                sds.spice_indexer.index_pointing_data(key)
                sds.spice_indexer.index_repoint_file(key)
            elif kernel_type == "thruster":
                sds.spice_indexer.index_small_forces_file(key)
            else:
                sds.spice_indexer.index_spice_file(key)
        except Exception as err:
            # One unreadable kernel should not cost the rest of the run.
            failed.append((path, err))
            print(f"  FAILED {path.name}: {err}")
            continue
        indexed.append(path)
        print(f"  {path.name}")

    return indexed, failed


def main():
    """Index everything on disk that is not in the database yet."""
    args = parse_args()
    load_sds_environment()
    sds = import_sds()
    redirect_s3_to_local(sds)

    if args.create_tables:
        create_tables(sds)
    require_tables(sds)

    spice_paths, other_paths = collect_files(args.path)

    print(f"non-SPICE files on disk ({len(other_paths)})")
    indexed_other = index_regular_files(sds, other_paths, args.dry_run)

    print(f"SPICE files on disk ({len(spice_paths)})")
    indexed_spice, failed = index_spice_files(
        sds, spice_paths, args.dry_run, args.force
    )

    verb = "would index" if args.dry_run else "indexed"
    seen = len(other_paths) + len(spice_paths)
    new = len(indexed_other) + len(indexed_spice)
    print(
        f"{seen} file(s) on disk, {verb} {new} "
        f"({len(indexed_other)} non-SPICE, {len(indexed_spice)} SPICE), "
        f"{seen - new - len(failed)} already indexed or not indexable, "
        f"{len(failed)} failed"
    )


if __name__ == "__main__":
    main()
