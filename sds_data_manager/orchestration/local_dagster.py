"""Local Dagster entrypoint.

Swaps the AWS Batch / ECR / SQS clients for local stand-ins and then re-exports
the normal Definitions, so the pipeline can be driven without AWS.

Point Dagster at this module instead of imap_dagster.py:

    dagster dev -w sds_data_manager/orchestration/workspace.local.yaml

Set IMAP_RUN_JOBS_LOCALLY=1 to actually execute submitted jobs through
``python -m imap_processing.cli``. Without it (the default) commands are logged
and recorded but never run, which is the safe way to watch what the pipeline
decides to do.
"""

import logging
import os

from sds_data_manager.lambda_code.SDSCode.local_clients import (
    LocalBatchClient,
    LocalECRClient,
    LocalSQSClient,
    local_upload_dependency_file,
)
from sds_data_manager.lambda_code.SDSCode.local_indexer import handle_job_completion
from sds_data_manager.orchestration import imap_job, reprocessing

logger = logging.getLogger(__name__)

RUN_JOBS_LOCALLY = os.getenv("IMAP_RUN_JOBS_LOCALLY", "").lower() in (
    "1",
    "true",
    "yes",
)

# These are module-level globals in imap_job/reprocessing and are looked up at
# call time, so rebinding them here takes effect no matter the import order.
BATCH_CLIENT = LocalBatchClient(
    run_locally=RUN_JOBS_LOCALLY,
    # Stands in for the S3 event -> indexer lambda hop that would otherwise put
    # a job's outputs into science_files, which is what the next level's sensors
    # poll. Without it the pipeline stops after one level.
    on_complete=handle_job_completion,
)
ECR_CLIENT = LocalECRClient()
SQS_CLIENT = LocalSQSClient()

imap_job.BATCH_CLIENT = BATCH_CLIENT
imap_job.ECR_CLIENT = ECR_CLIENT
imap_job.SQS_CLIENT = SQS_CLIENT
reprocessing.SQS_CLIENT = SQS_CLIENT

# Write dependency JSON to the local data directory instead of presigning an S3
# upload. imap_data_access.download() returns early when the file already exists,
# so imap_cli resolves --dependency from disk with no network call.
imap_job.IMAPJobHandler.upload_dependency_file = local_upload_dependency_file

logger.info(
    "Local AWS client stand-ins installed (jobs will %s).",
    "RUN locally" if RUN_JOBS_LOCALLY else "be recorded only",
)

# Imported after patching purely for readability; the rebinding above is what
# matters, not the ordering.
from sds_data_manager.orchestration.imap_dagster import defs  # noqa: E402

__all__ = ["defs"]
