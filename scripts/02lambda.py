import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import imap_data_access
from imap_data_access import ScienceFilePath
from imap_data_access.file_validation import generate_imap_file_path
from imap_processing.cli import main as imap_cli_main
from sqlalchemy.orm import sessionmaker

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
            file_path=str(generate_imap_file_path("imap_swe_l1b-in-flight-cal_20240510_20260716_v019.csv").construct_path()),
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
        ScienceFiles(
            file_path=str(generate_imap_file_path("imap_swe_l0_raw_20251125_v002.pkts").construct_path()),
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 11, 25),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2026-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]

    with sessionmaker(bind=engine)() as session:
        session.add_all(records)
        session.commit()


class Client:
    def delete_message(self, *args, **kwargs):
        pass

    def upload_dependency_file(self, dependency_file_path, serialized_dependencies):
        full_path = generate_imap_file_path(dependency_file_path.name).construct_path()
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(serialized_dependencies)
        return {"statusCode": 200, "body": str(full_path)}

    def submit_job(self, containerOverrides: dict, **kwargs):
        command_args = containerOverrides["command"]
        if "--upload-to-sdc" in command_args:
            command_args.remove("--upload-to-sdc")
        old_argv = sys.argv
        sys.argv = [sys.argv[0]] + command_args
        try:
            imap_cli_main()
        finally:
            sys.argv = old_argv


local_client = Client()


if __name__ == "__main__":
    init_db()
    engine = database.get_engine()

    # Seed queue with the initial trigger file
    queue = ["imap_swe_l0_raw_20251125_v002.pkts"]
    processed = set()

    while queue:
        filename = queue.pop(0)
        if filename in processed:
            continue
        processed.add(filename)

        # Index the file into the DB (mimics indexer.s3_event_handler).
        # Skips files already seeded by init_db() via the existence check.
        file_path_str = str(
            generate_imap_file_path(filename).construct_path()
        )
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
                existing = session.query(ScienceFiles).filter_by(
                    file_path=file_path_str
                ).first()
                if not existing:
                    session.add(ScienceFiles(**sci_params))
                    session.commit()
        except Exception:
            pass  # not a science file (e.g. dependency JSON), skip

        # Snapshot data dir before triggering so we can detect new output files.
        data_dir = Path(imap_data_access.config["DATA_DIR"])
        before = set(data_dir.rglob("imap_*.*"))

        # Trigger batch_starter (mimics SQS → batch_starter chain).
        events = {
            "Records": [
                {
                    "body": json.dumps({"detail": {"object": {"key": filename}}}),
                    "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:",
                    "receiptHandle": filename,
                }
            ]
        }
        lambda_handler(events=events, context=None, client=local_client)

        # Enqueue any files produced by this job for downstream processing.
        after = set(data_dir.rglob("imap_*.*"))
        for new_file in after - before:
            queue.append(new_file.name)
