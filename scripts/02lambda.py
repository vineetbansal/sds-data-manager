import sys
from datetime import datetime

from imap_data_access.file_validation import generate_imap_file_path
from imap_processing.cli import main as imap_cli_main
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.database import database
from sds_data_manager.lambda_code.SDSCode.database.models import (
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
            file_path="/media/vineetb/delta/projects/imap/data/sds/imap/spice/lsk/naif0012.tls",
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
            sclk_kernel="/media/vineetb/delta/projects/imap/data/sds/imap/spice/sclk/imap_sclk_0145.tsc",
            lsk_kernel="/media/vineetb/delta/projects/imap/data/sds/imap/spice/lsk/naif0012.tls",
            version=12,
        ),
        SPICEFiles(
            file_name="imap_sclk_0145.tsc",
            file_path="/media/vineetb/delta/projects/imap/data/sds/imap/spice/sclk/imap_sclk_0145.tsc",
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
            sclk_kernel="/media/vineetb/delta/projects/imap/data/sds/imap/spice/sclk/imap_sclk_0145.tsc",
            lsk_kernel="/media/vineetb/delta/projects/imap/data/sds/imap/spice/lsk/naif0012.tls",
            version=145,
        ),
        ScienceFiles(
            file_path="/media/vineetb/delta/projects/imap/data/sds/imap/swe/l0/2025/10/imap_swe_l0_raw_20251007_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 10, 7),
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

    events = {
        "Records": [
            {
                # body is a json string
                # body[detail][object][key] needs to follow the conventional name format
                "body": '{ "detail": { "object": { "key": "imap_swe_l0_raw_20251007_v001.pkts" } } }',
                # URL that helps us create a unique queue URL
                "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:",
                "receiptHandle": "blah",
            }
        ]
    }

    lambda_handler(events=events, context=None, client=local_client)
