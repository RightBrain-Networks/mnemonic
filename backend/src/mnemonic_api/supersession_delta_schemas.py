"""Read-only evidence comparing successive revisions of the superseding document."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt

from mnemonic_api.decision_link_schemas import SupersessionDetail
from mnemonic_api.schemas import APIModel

Reference = Annotated[str, Field(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$")]


class SupersedesSection(APIModel):
    found: bool
    section_count: Annotated[StrictInt, Field(ge=0)]
    text: str | None = Field(default=None, exclude_if=lambda value: value is None)
    truncated: bool
    source_characters_scanned: Annotated[StrictInt, Field(ge=0, le=65536)]


class SupersessionDelta(APIModel):
    source: Literal["read-projection"] = "read-projection"
    target_reference: Reference
    before_reference: Reference | None
    after_reference: Reference
    kind: Literal["new-supersession", "continued-supersession"]
    details_before: SupersessionDetail | None
    details_after: SupersessionDetail | None
    metadata_changed: bool
    section_before: SupersedesSection
    section_after: SupersedesSection
    unified_diff: str | None = Field(default=None, exclude_if=lambda value: value is None)
    truncated: bool
    section_scan_limit: Literal[65536] = 65536
    section_byte_limit: Literal[8192] = 8192
    diff_byte_limit: Literal[16384] = 16384
    interpretation: Literal[
        "Supersedes headings are evidence aids; read exact revisions to establish rule scope."
    ] = "Supersedes headings are evidence aids; read exact revisions to establish rule scope."
