import requests
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from pathlib import Path
import imap_data_access
from sds_data_manager.lambda_code.SDSCode.database import database
from sds_data_manager.lambda_code.SDSCode.database.models import Base
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer import s3_event_handler

# Monkey patch
import sds_data_manager.lambda_code.SDSCode.spice_utilities as spice_utilities_mod
spice_utilities_mod.download_from_s3 = lambda file_path, bucket_name=None: imap_data_access.config["DATA_DIR"] / file_path
import sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer as indexer_mod
indexer_mod.get_file_ingestion_date = lambda file_path: datetime.now(tz=timezone.utc)
import sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer as spice_indexer_mod
spice_indexer_mod.download_from_s3 = lambda file_path: Path(file_path)


RESET = False
INDEX_SPICE = False
INDEX_REPOINT = False
INDEX_ANCILLARY = False
INDEX_SCIENCE = False
INDEX_QUICKLOOK = False


if __name__ == "__main__":

    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    end = datetime.now(tz=timezone.utc)

    start_time = int(start.timestamp())
    end_time = int(end.timestamp())

    # --------------- Create database tables ---------------- #
    if RESET:
        engine = database.get_engine()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    # --------------- Download and index SPICE files ---------------- #
    if INDEX_SPICE:
        url = f"https://api.imap-mission.com/spice-query?start_time={start_time}&end_time={end_time}"
        request = requests.Request("GET", url).prepare()
        if imap_data_access.config["API_KEY"]:
            request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

        results = {}
        with requests.Session() as session:
            response = session.send(request)
            response.raise_for_status()
            results = response.json()

        print(f"Found {len(results)} SPICE files")

        for result in results:
            download_path = imap_data_access.download(result["file_name"])
            print(download_path)

            spice_indexer_mod.index_spice_file(result["file_name"])

    # --------------- Index downloaded repoint file ---------------- #
    if INDEX_REPOINT:
        spice_indexer_mod.index_pointing_data("imap_2026_091_01.repoint.csv")

    if INDEX_ANCILLARY:
        results = imap_data_access.query(table="ancillary", start_date=start.strftime("%Y%m%d"))
        for result in results:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))

            event = {"detail": {"object": {"key": file_path}}}
            s3_event_handler(event)

    if INDEX_SCIENCE:
        results = imap_data_access.query(table="science", data_level="l0", start_date=start.strftime("%Y%m%d"))
        for result in results:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))

            event = {"detail": {"object": {"key": file_path}}}
            s3_event_handler(event)