import requests
from datetime import datetime, timezone
from pathlib import Path
import imap_data_access
from sqlalchemy.exc import IntegrityError
from sds_data_manager.lambda_code.SDSCode.database import database
from sds_data_manager.lambda_code.SDSCode.database.models import Base
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer import s3_event_handler
import sds_data_manager.lambda_code.SDSCode.spice_utilities as spice_utilities_mod
import sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer as indexer_mod
import sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer as spice_indexer_mod

# Redirect S3 operations to the local filesystem for offline use
spice_utilities_mod.download_from_s3 = lambda file_path, bucket_name=None: imap_data_access.config["DATA_DIR"] / file_path
indexer_mod.get_file_ingestion_date = lambda file_path: datetime.now(tz=timezone.utc)
spice_indexer_mod.get_file_ingestion_date = lambda file_path: datetime.now(tz=timezone.utc)
spice_indexer_mod.download_from_s3 = lambda file_path: imap_data_access.config["DATA_DIR"] / Path(file_path)

# Flags to control which database indexing sections run
DOWNLOAD = True
RESET = True
INDEX_SPICE = True
INDEX_ANCILLARY = True
INDEX_SCIENCE = True


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

        for result in results:
            download_path = imap_data_access.download(result["file_path"])
            print(download_path)

        if INDEX_SPICE:
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
        url = f"https://api.imap-mission.com/spice-query?start_time={start_time}&end_time={end_time}"
        request = requests.Request("GET", url).prepare()
        if imap_data_access.config["API_KEY"]:
            request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

        with requests.Session() as session:
            response = session.send(request)
            response.raise_for_status()
            results = response.json()

        print(f"Found {len(results)} SPICE files to index")
        for result in results:
            download_path = imap_data_access.download(result["file_name"])
            print(download_path)
            spice_indexer_mod.index_spice_file(result["file_name"])

    # Download and index ancillary files
    if INDEX_ANCILLARY:
        results = imap_data_access.query(table="ancillary", start_date=start.strftime("%Y%m%d"))
        for result in results:
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
        results = imap_data_access.query(
            table="science",
            data_level="l0",
            start_date=start.strftime("%Y%m%d"),
        )
        for result in results:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))
            event = {"detail": {"object": {"key": file_path}}}
            try:
                s3_event_handler(event)
            except IntegrityError:
                print(f"  Skipping {downloaded_file_path} (already indexed)")
