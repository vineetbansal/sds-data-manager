"""Local drop-in replacements for AWS boto3 clients.

These classes intercept boto3 API calls and redirect them to local execution,
making it possible to run the orchestration code without real AWS infrastructure.

They are only wired in by ``sds_data_manager.orchestration.local_dagster``; nothing
imports them on the deployed path, so AWS behavior is unchanged.
"""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches the shape of a real ECR image URI so that the parsing in
# imap_job._get_container_image_digest works unmodified:
#   <registryId>.dkr.ecr.<region>.amazonaws.com/<repositoryName>:<tag>
LOCAL_REGISTRY_ID = "000000000000"
LOCAL_REGISTRY = f"{LOCAL_REGISTRY_ID}.dkr.ecr.us-west-2.amazonaws.com"


class ImageNotFoundException(Exception):
    """Stand-in for ECR_CLIENT.exceptions.ImageNotFoundException."""


class _Exceptions:
    """Namespace mimicking a boto3 client's ``.exceptions`` attribute."""

    ImageNotFoundException = ImageNotFoundException


class LocalECRClient:
    """Intercept ECR API calls and return synthetic image metadata.

    Replace ``imap_job.ECR_CLIENT`` with an instance of this class.
    """

    exceptions = _Exceptions

    def __init__(self, processing_version: str | None = None):
        """Record the imap_processing version the digest should be derived from.

        Parameters
        ----------
        processing_version : str, optional
            Version string to fold into the synthetic digest. Defaults to the
            installed imap_processing version.
        """
        if processing_version is None:
            try:
                import imap_processing

                processing_version = imap_processing.__version__
            except ImportError:
                processing_version = "unknown"
        self.processing_version = processing_version

    def describe_images(self, registryId, repositoryName, imageIds):  # noqa: N803
        """Return a stable synthetic digest for a local "image".

        The digest is derived from the repository, tag and installed
        imap_processing version. imap_job folds this digest into the dependency
        hash, which is how duplicate jobs are detected, so it must be stable
        across runs but must change when the processing code changes -
        otherwise upgrading imap_processing would leave old jobs looking
        identical and they would be skipped.
        """
        tag = imageIds[0].get("imageTag", "latest")
        seed = f"{repositoryName}:{tag}:{self.processing_version}"
        digest = hashlib.sha256(seed.encode()).hexdigest()
        return {"imageDetails": [{"imageDigest": f"sha256:{digest}"}]}


class LocalBatchClient:
    """Intercept AWS Batch API calls and run jobs locally via subprocess.

    Replace ``imap_job.BATCH_CLIENT`` with an instance of this class.
    """

    def __init__(
        self,
        run_locally: bool = False,
        timeout: int = 3600,
        allow_upload: bool = False,
        on_complete=None,
    ):
        """Configure how submitted jobs are handled.

        Parameters
        ----------
        run_locally : bool
            When True, submit_job actually runs imap_processing.cli in a
            subprocess and blocks until it finishes. When False (the default)
            the command is recorded and logged but not executed, which is the
            safer way to inspect what the pipeline wants to do.
        timeout : int
            Seconds to allow a locally executed job before killing it.
        allow_upload : bool
            imap_job always appends --upload-to-sdc, and imap-data-access is
            normally configured against the real SDC with a live API key, so a
            local run would push locally-produced files to production. The flag
            is stripped unless this is explicitly set True.
        on_complete : callable, optional
            Called with the submission record after a locally executed job
            finishes. Used to stand in for the S3/Batch events that would
            normally trigger the indexer lambda.
        """
        self.run_locally = run_locally
        self.timeout = timeout
        self.allow_upload = allow_upload
        self.on_complete = on_complete
        self.submitted: list[dict] = []
        self._counter = 0

    def describe_job_definitions(self, jobDefinitionName, status=None):  # noqa: N803
        """Return a synthetic ACTIVE job definition for any requested name.

        The image URI is built to look like a real ECR URI because
        imap_job._get_container_image_digest parses it apart to call ECR.
        """
        # "ProcessingJob-swapi" or "ProcessingJob-swapi-l3" -> "swapi"
        instrument = jobDefinitionName.split("-")[1]
        return {
            "jobDefinitions": [
                {
                    "jobDefinitionName": jobDefinitionName,
                    "revision": 1,
                    "status": "ACTIVE",
                    "containerProperties": {
                        "image": f"{LOCAL_REGISTRY}/{instrument}-repo:latest"
                    },
                }
            ]
        }

    def submit_job(
        self,
        jobName,  # noqa: N803
        jobQueue,  # noqa: N803
        jobDefinition,  # noqa: N803
        containerOverrides,  # noqa: N803
        retryStrategy=None,  # noqa: N803
        **kwargs,
    ):
        """Run the batch command locally instead of submitting it to AWS.

        The command built by imap_job is the imap_processing CLI argument list,
        so it is handed to ``python -m imap_processing.cli`` unchanged.
        """
        self._counter += 1
        command = list(containerOverrides["command"])
        if not self.allow_upload and "--upload-to-sdc" in command:
            command.remove("--upload-to-sdc")
            logger.info(
                "[local batch] stripped --upload-to-sdc from %s; pass "
                "allow_upload=True to publish to the real SDC",
                jobName,
            )
        record = {
            "jobName": jobName,
            "jobQueue": jobQueue,
            "jobDefinition": jobDefinition,
            "command": command,
        }
        self.submitted.append(record)

        argv = [sys.executable, "-m", "imap_processing.cli", *command]
        if not self.run_locally:
            logger.info(
                "[local batch] recorded (not executed) %s: %s",
                jobName,
                " ".join(argv),
            )
            return {"jobId": f"local-{self._counter}", "jobName": jobName}

        logger.info("[local batch] running %s: %s", jobName, " ".join(argv))
        result = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=os.environ.copy(),
            check=False,
        )
        record["returncode"] = result.returncode
        record["stdout"] = result.stdout
        record["stderr"] = result.stderr
        if result.returncode != 0:
            logger.error(
                "[local batch] %s failed (rc=%s)\n%s",
                jobName,
                result.returncode,
                result.stderr[-4000:],
            )
        else:
            logger.info("[local batch] %s finished successfully", jobName)

        if self.on_complete is not None:
            # Stands in for the S3 / Batch state-change events that trigger the
            # indexer lambda on AWS. Failing here must not look like a job
            # failure, so it is logged rather than raised.
            try:
                self.on_complete(record)
            except Exception:
                logger.exception("[local batch] on_complete failed for %s", jobName)

        return {"jobId": f"local-{self._counter}", "jobName": jobName}


class LocalUploadResponse:
    """Minimal stand-in for the requests.Response returned after an S3 upload."""

    def __init__(self, status_code: int, path):
        """Store the resulting status code and the path written to."""
        self.status_code = status_code
        self.path = path

    def __bool__(self):
        """Callers only test truthiness to decide whether the upload worked."""
        return 200 <= self.status_code < 300


def local_upload_dependency_file(self, dependency_file_path, serialized_dependencies):
    """Write a job's dependency JSON to the local data directory.

    Drop-in replacement for ``IMAPJobHandler.upload_dependency_file``, which
    normally asks the upload API for a presigned URL and PUTs the file to S3.

    The file is written to the path imap-data-access already computed for it,
    which is what makes this work end to end: ``imap_data_access.download()``
    returns early when the destination already exists, so imap_cli resolves
    ``--dependency <name>.json`` from disk without any network call.

    The real method raises KeyError if the file exists, which never happens on a
    fresh AWS container but happens constantly on reruns locally, so identical
    content is treated as reuse instead - mirroring the 409 branch upstream.
    """
    path = Path(dependency_file_path)

    if path.is_file():
        existing = path.read_text()
        if existing == serialized_dependencies:
            logger.info("[local upload] dependency file already present: %s", path)
            return LocalUploadResponse(200, path)
        logger.warning(
            "[local upload] overwriting dependency file with changed content: %s", path
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized_dependencies)
    logger.info("[local upload] wrote dependency file: %s", path)
    return LocalUploadResponse(200, path)


class LocalSQSClient:
    """Intercept SQS API calls for local running.

    Replace ``imap_job.SQS_CLIENT`` and ``reprocessing.SQS_CLIENT`` with an
    instance of this class. Messages are held in memory; receive_message
    returns nothing, so the reprocessing sensor simply finds an empty queue.
    """

    def __init__(self):
        """Start with an empty in-memory queue."""
        self.messages: list[dict] = []
        self.deleted: list[dict] = []

    @staticmethod
    def _ok(**extra):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}, **extra}

    def send_message(self, QueueUrl, MessageBody, **kwargs):  # noqa: N803
        """Record a message instead of sending it."""
        self.messages.append({"QueueUrl": QueueUrl, "Body": MessageBody})
        logger.info("[local sqs] send_message to %s: %s", QueueUrl, MessageBody)
        return self._ok(MessageId=f"local-{len(self.messages)}")

    def receive_message(self, QueueUrl, **kwargs):  # noqa: N803
        """Return no messages, so reprocessing sensors idle harmlessly."""
        return self._ok(Messages=[])

    def delete_message(self, QueueUrl, ReceiptHandle, **kwargs):  # noqa: N803
        """Record the delete instead of performing it."""
        self.deleted.append({"QueueUrl": QueueUrl, "ReceiptHandle": ReceiptHandle})
        return self._ok()
