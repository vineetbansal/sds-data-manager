import asyncio
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import imap_data_access
from imap_data_access import ScienceFilePath
from imap_data_access.file_validation import generate_imap_file_path
from sqlalchemy.orm import sessionmaker

from scripts.client import LocalClient
from sds_data_manager.lambda_code.SDSCode.database import database
from sds_data_manager.lambda_code.SDSCode.database.models import (
    AncillaryFiles,
    Base,
    ScienceFiles,
    SPICEFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    lambda_handler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db():
    engine = database.get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    records = [
        SPICEFiles(
            file_name="naif0012.tls",
            file_path=str(generate_imap_file_path("naif0012.tls").construct_path()),
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="naif.tls",
            kernel_type="leapseconds",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T12:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel=str(generate_imap_file_path("imap_sclk_0145.tsc").construct_path()),
            lsk_kernel=str(generate_imap_file_path("naif0012.tls").construct_path()),
            version=12,
        ),
        SPICEFiles(
            file_name="imap_sclk_0145.tsc",
            file_path=str(generate_imap_file_path("imap_sclk_0145.tsc").construct_path()),
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:01+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_sclk_0000.tsc",
            kernel_type="spacecraft_clock",
            min_date_j2000=315576066.1839245,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[315576066.1839245, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2010-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2010-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel=str(generate_imap_file_path("imap_sclk_0145.tsc").construct_path()),
            lsk_kernel=str(generate_imap_file_path("naif0012.tls").construct_path()),
            version=145,
        ),
        AncillaryFiles(
            file_path=str(
                generate_imap_file_path(
                    "imap_swe_esa-lut_20250301_v001.csv"
                ).construct_path()
            ),
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2025, 3, 1),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:02+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path=str(
                generate_imap_file_path(
                    "imap_swe_eu-conversion_20240510_v001.csv"
                ).construct_path()
            ),
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2024, 5, 10),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:03+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path=str(
                generate_imap_file_path(
                    "imap_swe_l1b-in-flight-cal_20240510_20260716_v019.csv"
                ).construct_path()
            ),
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2024, 5, 10),
            end_date=datetime(2026, 7, 16),
            version="v019",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:04+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]

    with sessionmaker(bind=database.get_engine())() as session:
        session.add_all(records)
        session.commit()


def _index_file(filename: str, engine) -> None:
    file_path_str = str(generate_imap_file_path(filename).construct_path())
    try:
        file_obj = ScienceFilePath(filename)
        sci_params = file_obj.extract_filename_components(filename)
        sci_params.pop("mission")
        sci_params["start_date"] = datetime.strptime(
            sci_params.pop("start_date"), "%Y%m%d"
        )
        sci_params["file_path"] = file_path_str
        sci_params["ingestion_date"] = datetime.now(tz=timezone.utc)
        with sessionmaker(bind=engine)() as session:
            if not session.query(ScienceFiles).filter_by(file_path=file_path_str).first():
                session.add(ScienceFiles(**sci_params))
                session.commit()
    except Exception:
        # Not a science file (e.g. a dependency JSON produced by the job) — skip.
        pass


async def _run_and_detect_outputs(coro) -> list[str]:
    data_dir = Path(imap_data_access.config["DATA_DIR"])
    before = {p: p.stat().st_mtime for p in data_dir.rglob("imap_*.cdf")}
    await coro
    after = {p: p.stat().st_mtime for p in data_dir.rglob("imap_*.cdf")}
    return [p.name for p, mtime in after.items() if before.get(p) != mtime]



def _fifo_reader(fifo_path: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
    while True:
        try:
            with open(fifo_path) as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.error("Malformed JSON: %s", raw)
                        event = {}
                    loop.call_soon_threadsafe(queue.put_nowait, event)
        except OSError as exc:
            logger.error("FIFO read error: %s", exc)
            break


async def run(fifo_path: str, initial_filenames: list[str]) -> None:
    init_db()
    engine = database.get_engine()
    client = LocalClient()
    queue: asyncio.Queue = asyncio.Queue()
    processed: set[str] = set()

    for filename in initial_filenames:
        await queue.put({"name": "file", "filename": filename})

    loop = asyncio.get_running_loop()
    if not os.path.exists(fifo_path):
        os.mkfifo(fifo_path)
    thread = threading.Thread(
        target=_fifo_reader, args=(fifo_path, queue, loop), daemon=True
    )
    thread.start()
    logger.info("Listening for events on %s", fifo_path)

    while True:
        event = await queue.get()
        match event.get("name"):

            case "file":
                filename = event.get("filename")
                if not filename or filename in processed:
                    continue
                processed.add(filename)
                logger.info("Processing: %s", filename)
                await loop.run_in_executor(None, _index_file, filename, engine)
                bs_event = {
                    "Records": [
                        {
                            "body": json.dumps({"detail": {"object": {"key": filename}}}),
                            "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:",
                            "receiptHandle": filename,
                        }
                    ]
                }
                try:
                    new_files = await _run_and_detect_outputs(
                        loop.run_in_executor(None, lambda_handler, bs_event, None, client)
                    )
                except Exception:
                    logger.exception("Error processing %s", filename)
                    continue

                for new_file in new_files:
                    logger.info("Enqueuing output: %s", new_file)
                    await queue.put({"name": "file", "filename": new_file})

            case "cadence":
                cadence = event.get("cadence")
                logger.info("Cadence processing: %s", cadence)
                loop = asyncio.get_running_loop()
                try:
                    new_files = await _run_and_detect_outputs(
                        loop.run_in_executor(
                            None,
                            lambda_handler,
                            {"cadence": cadence},
                            None,
                            client,
                        )
                    )
                except Exception:
                    logger.exception("Error during cadence processing")
                    continue

                for new_file in new_files:
                    logger.info("Enqueuing output: %s", new_file)
                    await queue.put({"name": "file", "filename": new_file})

            case "reprocess":
                qsp = {
                    "reprocessing": True,
                    "start_date": event.get("start_date"),
                    "end_date": event.get("end_date"),
                    "instrument": event.get("instrument"),
                    "data_level": event.get("data_level"),
                    "descriptor": event.get("descriptor"),
                }
                logger.info("Bulk reprocessing: %s", qsp)
                loop = asyncio.get_running_loop()
                try:
                    new_files = await _run_and_detect_outputs(
                        loop.run_in_executor(
                            None,
                            lambda_handler,
                            {"queryStringParameters": qsp},
                            None,
                            client,
                        )
                    )
                except Exception:
                    logger.exception("Error during bulk reprocessing")
                    continue

                for new_file in new_files:
                    logger.info("Enqueuing output: %s", new_file)
                    await queue.put({"name": "file", "filename": new_file})

            case _:
                logger.error("Unknown event: %s", event)


if __name__ == "__main__":
    fifo = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pipeline.fifo"
    asyncio.run(run(fifo_path=fifo, initial_filenames=[]))

    # In another terminal:
    #   echo '{"name": "file", "filename": "imap_swe_l0_raw_20251125_v002.pkts"}' > /tmp/pipeline.fifo
    #   echo '{"name": "reprocess", "start_date": "20251125", "end_date": "20251125", "instrument": "swe", "data_level": "l1a", "descriptor": "sci"}' > /tmp/pipeline.fifo
    #   echo '{"name": "cadence", "cadence": "1mo"}' > /tmp/pipeline.fifo
