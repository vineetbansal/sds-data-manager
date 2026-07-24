"""Local stand-in for the indexer lambda.

On AWS, a processing job uploads its outputs to S3, an S3 event triggers
``pipeline_lambdas/indexer.py``, and that writes a row into science_files. Those
rows are what the next tier of sensors polls, so the indexer is the only thing
connecting one processing level to the next.

Locally there is no S3 and no event, so nothing closes that loop. This module
indexes files straight from the imap-data-access data directory instead, using
the same filename parsing and the same tables as the real indexer.

Only ``local_dagster`` wires this in; the deployed path never imports it.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import imap_data_access
from imap_data_access import (
    AncillaryFilePath,
    QuicklookFilePath,
    ReleaseFilePath,
    ScienceFilePath,
)
from imap_data_access.file_validation import generate_imap_file_path

from .database import database as db
from .database import models

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    """Return the configured imap-data-access data directory."""
    return Path(imap_data_access.config["DATA_DIR"])


def _relative_path(path: Path) -> str:
    """Return the path as stored in the database.

    The real indexer stores the S3 object key, which is the data directory
    relative path, e.g. ``imap/swe/l1a/2026/04/imap_swe_l1a_hk_...cdf``.
    """
    return Path(path).relative_to(_data_dir()).as_posix()


def _table_for(file_obj):
    """Return the model matching this file type, or None if not indexed here.

    Order matters and mirrors the real indexer: QuicklookFilePath inherits from
    ScienceFilePath, and ReleaseFilePath inherits from AncillaryFilePath, so the
    subclasses have to be checked first.
    """
    if isinstance(file_obj, QuicklookFilePath):
        return models.QuicklookFiles
    if isinstance(file_obj, ScienceFilePath):
        return models.ScienceFiles
    if isinstance(file_obj, ReleaseFilePath):
        return models.ReleaseFiles
    if isinstance(file_obj, AncillaryFilePath):
        return models.AncillaryFiles
    return None


def index_file(path, session) -> bool:
    """Index a single local file into the appropriate table.

    Parameters
    ----------
    path : pathlib.Path or str
        Path to a file inside the imap-data-access data directory.
    session : sqlalchemy.orm.Session
        Open session to write through.

    Returns
    -------
    bool
        True if a new row was inserted, False if skipped or already present.
    """
    path = Path(path)
    filename = path.name

    try:
        file_obj = generate_imap_file_path(filename)
    except ValueError:
        logger.debug("[local index] not a recognised IMAP filename: %s", filename)
        return False

    # IDEX L0 is indexed by a separate lambda on AWS, so leave it alone here too.
    if (
        type(file_obj) is ScienceFilePath
        and file_obj.instrument == "idex"
        and file_obj.data_level == "l0"
    ):
        return False

    table = _table_for(file_obj)
    if table is None:
        return False

    relative = _relative_path(path)
    if session.get(table, relative) is not None:
        return False

    params = file_obj.extract_filename_components(filename)
    params.pop("mission", None)
    params["start_date"] = datetime.strptime(params.pop("start_date"), "%Y%m%d")
    params["file_path"] = relative
    # The real indexer uses the S3 object's LastModified; mtime is the local
    # equivalent. Sensors order and filter on this, so it must be set.
    params["ingestion_date"] = datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    if params.get("end_date"):
        params["end_date"] = datetime.strptime(params.pop("end_date"), "%Y%m%d")

    row = table(**params)
    if table is models.ScienceFiles:
        row.crid = None
    session.add(row)
    logger.info("[local index] indexed %s into %s", filename, table.__tablename__)
    return True


def index_new_files(instrument: str | None = None, data_level: str | None = None):
    """Index any files on disk that are not yet in the database.

    Parameters
    ----------
    instrument : str, optional
        Restrict the scan to one instrument. Scanning everything is slow, so
        callers that know what a job produced should pass this.
    data_level : str, optional
        Restrict the scan to one data level.

    Returns
    -------
    list[str]
        Filenames that were newly indexed.
    """
    root = _data_dir() / "imap"
    if instrument:
        root = root / instrument
        if data_level:
            root = root / data_level
    if not root.exists():
        logger.info("[local index] nothing to scan, no such path: %s", root)
        return []

    indexed = []
    with db.Session() as session:
        for path in sorted(root.rglob("*")):
            if path.is_file() and index_file(path, session):
                indexed.append(path.name)
        session.commit()

    logger.info("[local index] indexed %d new file(s) under %s", len(indexed), root)
    return indexed


def complete_processing_job(job_name: str, succeeded: bool):
    """Mark a processing_job_table row SUCCEEDED or FAILED.

    On AWS a Batch state-change event drives this. The row id is the last
    segment of the job name, the same convention batch_event_handler relies on.
    """
    job_id = job_name.rsplit("-", 1)[-1]
    if not job_id.isdigit():
        logger.debug("[local index] no job id in job name: %s", job_name)
        return

    status = models.Status.SUCCEEDED if succeeded else models.Status.FAILED
    with db.Session() as session:
        job = session.get(models.ProcessingJob, int(job_id))
        if job is None:
            logger.warning("[local index] no processing job with id %s", job_id)
            return
        job.status = status
        job.stopped_at = datetime.now(tz=timezone.utc)
        session.commit()
    logger.info("[local index] processing job %s -> %s", job_id, status)


def handle_job_completion(record: dict):
    """Close the loop after a locally executed job.

    Wired in as LocalBatchClient's on_complete callback: updates the job row and
    indexes whatever the job wrote, so the next level's sensors can see it.
    """
    succeeded = record.get("returncode") == 0
    complete_processing_job(record.get("jobName", ""), succeeded)

    if not succeeded:
        logger.info("[local index] job failed, nothing to index")
        return

    command = record.get("command", [])
    instrument = data_level = None
    for flag, value in zip(command, command[1:], strict=False):
        if flag == "--instrument":
            instrument = value
        elif flag == "--data-level":
            data_level = value

    index_new_files(instrument=instrument, data_level=data_level)
