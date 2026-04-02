"""Abstract pipeline client with local and AWS implementations."""

import abc
import sys
from pathlib import Path

from imap_data_access.file_validation import generate_imap_file_path
from imap_processing.cli import main as imap_cli_main


class BaseClient(abc.ABC):
    """Abstract client for pipeline operations.

    The three methods here mirror the interface that batch_starter's
    lambda_handler, try_to_submit_job, and s3_processing_event call when
    a non-None client is injected.  Both concrete subclasses must implement
    all three.
    """

    @abc.abstractmethod
    def submit_job(self, **kwargs) -> None:
        """Submit a processing job.

        Called by batch_starter with keyword args: jobName, jobQueue,
        jobDefinition, containerOverrides (dict with "command" key),
        retryStrategy.
        """

    @abc.abstractmethod
    def delete_message(self, **kwargs) -> None:
        """Delete a processed message from the queue.

        Called by batch_starter with keyword args: QueueUrl, ReceiptHandle.
        """

    @abc.abstractmethod
    def upload_dependency_file(
        self, dependency_file_path: Path, serialized_dependencies: str
    ) -> dict:
        """Write or upload a job dependency JSON file.

        Returns a dict with at least ``statusCode`` and ``body`` keys,
        matching the upload_api response contract used by batch_starter.
        """


class LocalClient(BaseClient):
    """Pipeline client for local execution.

    Jobs are run in-process via imap_processing.cli.  Dependency files are
    written directly to the local DATA_DIR filesystem.  SQS delete_message
    is a no-op since there is no local message broker.

    Note: batch_starter's GLOWS l3e code path calls submit_all_jobs without
    propagating the client kwarg (batch_starter.py ~line 900), so that branch
    will still attempt to reach the real AWS Batch endpoint even with this
    client injected.
    """

    def delete_message(self, **kwargs) -> None:
        pass

    def upload_dependency_file(
        self, dependency_file_path: Path, serialized_dependencies: str
    ) -> dict:
        full_path = generate_imap_file_path(dependency_file_path.name).construct_path()
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(serialized_dependencies)
        return {"statusCode": 200, "body": str(full_path)}

    def submit_job(self, **kwargs) -> None:
        command_args = list(kwargs["containerOverrides"]["command"])
        if "--upload-to-sdc" in command_args:
            command_args.remove("--upload-to-sdc")
        old_argv = sys.argv
        sys.argv = [sys.argv[0]] + command_args
        try:
            imap_cli_main()
        finally:
            sys.argv = old_argv


class AWSClient(BaseClient):
    """Pipeline client backed by real AWS services.

    Delegates submit_job to boto3 Batch and delete_message to boto3 SQS.

    upload_dependency_file raises NotImplementedError: when an AWSClient is
    passed to batch_starter, the upload_dependency_file code path in
    batch_starter (client=None branch) handles S3 uploads directly via
    upload_api.  This method should never be reached.
    """

    def __init__(self, region: str = "us-west-2") -> None:
        import boto3

        self._batch = boto3.client("batch", region_name=region)
        self._sqs = boto3.client("sqs", region_name=region)

    def submit_job(self, **kwargs) -> dict:
        return self._batch.submit_job(**kwargs)

    def delete_message(self, **kwargs) -> dict:
        return self._sqs.delete_message(**kwargs)

    def upload_dependency_file(
        self, dependency_file_path: Path, serialized_dependencies: str
    ) -> dict:
        raise NotImplementedError(
            "AWSClient.upload_dependency_file should never be called; "
            "batch_starter handles S3 uploads internally when client=None."
        )
