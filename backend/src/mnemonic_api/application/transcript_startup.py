"""Transcript filesystem outages must remain diagnosable from the dashboard."""

import logging

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_storage import check_transcript_source

logger = logging.getLogger(__name__)


def start_transcript_services(config: Settings, index: ArtifactSearchIndex) -> None:
    for source in config.transcript_source_dirs:
        try:
            check_transcript_source(source, config.transcript_allowed_roots)
        except RuntimeError:
            logger.warning("Transcript source unavailable; see transcript health for diagnostics")
    try:
        index.start()
    except ApplicationError:
        logger.warning("Transcript index unavailable; dashboard remains available for diagnosis")
