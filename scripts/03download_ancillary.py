from pprint import pprint
import imap_data_access


calibration_files = imap_data_access.query(
    table="ancillary",
    instrument="swe",
    descriptor="eu-conversion",
    version="latest",
)

pprint(calibration_files)

calibration_file = sorted(
    calibration_files, key=lambda x: (x["start_date"], x["version"]), reverse=True
)[0]

download_path = imap_data_access.download(calibration_file["file_path"])
print(download_path)