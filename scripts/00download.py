import os.path
import requests
from datetime import datetime, timezone
from pathlib import Path
import imap_data_access
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sds_data_manager.lambda_code.SDSCode.database import database
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import Base
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer import \
    s3_event_handler
import sds_data_manager.lambda_code.SDSCode.spice_utilities as spice_utilities_mod
import sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer as indexer_mod
import \
    sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer as spice_indexer_mod

# Redirect S3 operations to the local filesystem for offline use
spice_utilities_mod.download_from_s3 = lambda file_path, bucket_name=None: \
    imap_data_access.config["DATA_DIR"] / file_path
indexer_mod.get_file_ingestion_date = lambda file_path: datetime.now(tz=timezone.utc)
spice_indexer_mod.get_file_ingestion_date = lambda file_path: datetime.now(
    tz=timezone.utc)
spice_indexer_mod.download_from_s3 = lambda file_path: imap_data_access.config[
                                                           "DATA_DIR"] / "imap" / "spice" / Path(file_path)
spice_indexer_mod.clear_ephemeral_storage = lambda downloaded_path: None


# Flags to control which database indexing sections run
DOWNLOAD = True
RESET = False
INDEX_SPICE = True
INDEX_ANCILLARY = True
INDEX_SCIENCE = True

# from imap_data_access/file_validation.py using _SPICE_DIR_MAPPING
# make sure leapseconds, spacecraft_clock are the first 2 entries.
SPICE_TYPES = ['leapseconds', 'spacecraft_clock', 'attitude_history',
               'pointing_attitude', 'attitude_predict', 'spin',
               'repoint', 'ephemeris_reconstructed', 'ephemeris_nominal',
               'ephemeris_predicted', 'ephemeris_90days', 'ephemeris_long',
               'ephemeris_launch', 'planetary_ephemeris', 'planetary_constants',
               'imap_frames', 'science_frames',
               'metakernel', 'thruster', 'lagrange_point', 'earth_attitude']


def get_indexed_basenames(model_class, column_name="file_path"):
    """Return set of already-indexed file basenames from a database table."""
    with database.Session() as session:
        col = getattr(model_class, column_name)
        rows = session.execute(select(col)).fetchall()
    return {os.path.basename(row[0]) for row in rows}


if __name__ == "__main__":

    if RESET:
        engine = database.get_engine()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    if not DOWNLOAD:
        imap_data_access.download = lambda file_path: Path(file_path)

    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    end = datetime.now(tz=timezone.utc)

    start_time = int(start.timestamp())
    end_time = int(end.timestamp())

    # --------------- Download L0 data for all instruments --------------- #
    for instrument in imap_data_access.VALID_INSTRUMENTS:
        print(instrument)
        results = imap_data_access.query(
            instrument=instrument,
            data_level="l0",
            start_date=start.strftime("%Y%m%d"),
        )
        for result in results:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))

    # --------------- Download SPICE kernel files --------------- #
    url = f"https://api.imap-mission.com/spice-query?start_time={start_time}&end_time={end_time}"
    request = requests.Request("GET", url).prepare()
    if imap_data_access.config["API_KEY"]:
        request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

    with requests.Session() as session:
        response = session.send(request)
        response.raise_for_status()
        results = response.json()

    print(f"Found {len(results)} SPICE files")
    for result in results:
        download_path = imap_data_access.download(result["file_name"])
        print(download_path)

    # Download spin, repoint, and thruster (small forces) tables
    for endpoint in ("spin-table", "repoint-table", "small-forces-table"):
        url = f"https://api.imap-mission.com/{endpoint}"
        request = requests.Request("GET", url).prepare()
        if imap_data_access.config["API_KEY"]:
            request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

        with requests.Session() as session:
            response = session.send(request)
            response.raise_for_status()
            results = response.json()

        if INDEX_SPICE:
            if endpoint == "spin-table":
                indexed = get_indexed_basenames(models.SpinFiles)
            elif endpoint == "repoint-table":
                indexed = get_indexed_basenames(models.RepointFiles)
            elif endpoint == "small-forces-table":
                indexed = get_indexed_basenames(models.SmallForcesFile)
            results = [r for r in results if os.path.basename(r["file_path"]) not in indexed]

        for result in results:
            download_path = imap_data_access.download(result["file_path"])
            print(download_path)

        if INDEX_SPICE and results:
            try:
                if endpoint == "spin-table":
                    spice_indexer_mod.index_spin_file(str(download_path))
                elif endpoint == "repoint-table":
                    spice_indexer_mod.index_pointing_data(str(download_path))
                    spice_indexer_mod.index_repoint_file(str(download_path))
                elif endpoint == "small-forces-table":
                    spice_indexer_mod.index_small_forces_file(str(download_path))
            except IntegrityError:
                print(f"  Skipping {download_path.name} (already indexed)")

    # --------------- Download science L0 files --------------- #
    results = imap_data_access.query(
        table="science",
        data_level="l0",
        start_date=start.strftime("%Y%m%d"),
    )
    for result in results:
        file_path = result["file_path"]
        downloaded_file_path = imap_data_access.download(file_path)
        print("  " + str(downloaded_file_path))

    # Index SPICE kernel files into the database
    if INDEX_SPICE:
        indexed_spice = get_indexed_basenames(models.SPICEFiles, "file_name")
        # Some spice types don't seem to return anything with start_time/end_time,
        # but do return records without it. Handle these first.
        for spice_type in SPICE_TYPES:
            url = f"https://api.imap-mission.com/spice-query?type={spice_type}"
            request = requests.Request("GET", url).prepare()
            if imap_data_access.config["API_KEY"]:
                request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

            with requests.Session() as session:
                response = session.send(request)
                response.raise_for_status()
                results = response.json()

            to_index = [r for r in results if os.path.basename(r["file_name"]) not in indexed_spice]
            print(f"Found {len(to_index)} new SPICE files to index")
            for result in to_index:
                download_path = imap_data_access.download(result["file_name"])
                print(download_path)
                spice_indexer_mod.index_spice_file(result["file_name"])
                indexed_spice.add(os.path.basename(result["file_name"]))

        # Now handle the ones that do respect start_time/end_time
        url = f"https://api.imap-mission.com/spice-query?start_time={start_time}&end_time={end_time}"
        request = requests.Request("GET", url).prepare()
        if imap_data_access.config["API_KEY"]:
            request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

        with requests.Session() as session:
            response = session.send(request)
            response.raise_for_status()
            results = response.json()

        to_index = [r for r in results if os.path.basename(r["file_name"]) not in indexed_spice]
        print(f"Found {len(to_index)} new SPICE files to index")
        for result in to_index:
            download_path = imap_data_access.download(result["file_name"])
            print(download_path)
            spice_indexer_mod.index_spice_file(result["file_name"])

    # Download and index ancillary files
    if INDEX_ANCILLARY:
        indexed_ancillary = get_indexed_basenames(models.AncillaryFiles)
        results = imap_data_access.query(table="ancillary",
                                         start_date=start.strftime("%Y%m%d"))
        to_index = [r for r in results if os.path.basename(r["file_path"]) not in indexed_ancillary]
        for result in to_index:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))
            event = {"detail": {"object": {"key": file_path}}}
            try:
                s3_event_handler(event)
            except IntegrityError:
                print(f"  Skipping {downloaded_file_path} (already indexed)")

    # Download and index science L0 files
    if INDEX_SCIENCE:
        indexed_science = get_indexed_basenames(models.ScienceFiles)
        results = imap_data_access.query(
            table="science",
            data_level="l0",
            start_date=start.strftime("%Y%m%d"),
        )
        to_index = [r for r in results if os.path.basename(r["file_path"]) not in indexed_science]
        for result in to_index:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))
            event = {"detail": {"object": {"key": file_path}}}
            try:
                s3_event_handler(event)
            except IntegrityError:
                print(f"  Skipping {downloaded_file_path} (already indexed)")
