"""Explicit, per-request human approval assertions; never authenticated identity."""

from pydantic import Field, StrictBool

from mnemonic_api.artifact_schemas import ArtifactActor


class ArtifactAccessRequest(ArtifactActor):
    approval_token: str | None = Field(default=None, min_length=32, max_length=128,
                                      pattern=r"^[A-Za-z0-9_-]+$")
    human_approved: StrictBool = False
