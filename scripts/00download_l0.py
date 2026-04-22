import imap_data_access
from datetime import datetime, timezone


if __name__ == "__main__":

    start = datetime(2026, 4, 1, tzinfo=timezone.utc)

    for instrument in imap_data_access.VALID_INSTRUMENTS:
        print(instrument)
        results = imap_data_access.query(instrument=instrument, data_level="l0", start_date=start.strftime("%Y%m%d"))
        for result in results:
            file_path = result["file_path"]
            downloaded_file_path = imap_data_access.download(file_path)
            print("  " + str(downloaded_file_path))
