import requests
from datetime import datetime, timezone
from pprint import pprint
import imap_data_access


if __name__ == "__main__":

    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime.now(tz=timezone.utc)

    start_time = int(start.timestamp())
    end_time = int(end.timestamp())

    # Add `&type=<type>` to the URL to filter by type
    types = ("science_frames", "spacecraft_clock", "planetary_ephemeris")

    url = f"https://api.imap-mission.com/spice-query?start_time={start_time}&end_time={end_time}"
    request = requests.Request("GET", url).prepare()
    if imap_data_access.config["API_KEY"]:
        # Add the API key to the request headers if it exists
        request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

    results = {}
    with requests.Session() as session:
        response = session.send(request)
        response.raise_for_status()

        results = response.json()
    print(len(results))

    for result in results:
        download_path = imap_data_access.download(result["file_name"])
        print(download_path)


    # spin/repoint/thruster data
    endpoints = ("spin-table", "repoint-table", "small-forces-table",)

    for endpoint in endpoints:
        url = f"https://api.imap-mission.com/{endpoint}"
        request = requests.Request("GET", url).prepare()
        if imap_data_access.config["API_KEY"]:
            # Add the API key to the request headers if it exists
            request.headers["x-api-key"] = imap_data_access.config["API_KEY"]

        with requests.Session() as session:
            response = session.send(request)
            response.raise_for_status()
            results = response.json()

        for result in results:
            download_path = imap_data_access.download(result["file_path"])
            print(download_path)