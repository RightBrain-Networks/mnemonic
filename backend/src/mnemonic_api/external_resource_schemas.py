"""Project resource definitions, public availability, and lease capabilities."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from mnemonic_api.schemas import (
    APIModel,
    ClientName,
    LeaseMinutes,
    LeaseSettingsRead,
    LeaseToken,
    SessionID,
    Title,
    _reject_explicit_null,
)

type ResourceQuantity = Annotated[int, Field(strict=True, ge=1, le=4294967295)]


class ExternalResourceDefinition(LeaseSettingsRead):
    name: Title
    capacity: ResourceQuantity


class ExternalResourceWrite(ExternalResourceDefinition):
    client_operation_id: UUID | None = Field(default=None, repr=False)


class ExternalLeaseRead(APIModel):
    id: UUID
    resource_id: UUID
    holder_client: ClientName
    holder_session_id: SessionID
    quantity: ResourceQuantity
    acquired_at: datetime
    expires_at: datetime


class ExternalResourceRead(ExternalResourceDefinition):
    id: UUID
    project_id: UUID
    available: int = Field(ge=0, le=4294967295)
    leases: list[ExternalLeaseRead]

    @model_validator(mode="after")
    def valid_allocation(self) -> Self:
        if self.available + sum(lease.quantity for lease in self.leases) != self.capacity:
            raise ValueError("Available and reserved units must add up to capacity")
        holders = {(lease.holder_client, lease.holder_session_id) for lease in self.leases}
        if len(holders) != len(self.leases) or any(
            lease.resource_id != self.id for lease in self.leases
        ):
            raise ValueError("List one lease per holder on this resource")
        return self


class ExternalResourceList(APIModel):
    project_id: UUID
    items: list[ExternalResourceRead]


class ExternalLeaseClaim(APIModel):
    holder_client: ClientName
    holder_session_id: SessionID
    claim_request_id: UUID = Field(repr=False)
    quantity: ResourceQuantity
    lease_minutes: LeaseMinutes | SkipJsonSchema[None] = None

    @model_validator(mode="before")
    @classmethod
    def duration_is_nonnull(cls, data: object) -> object:
        return _reject_explicit_null(data, "lease_minutes")


class ExternalLeaseClaimReceipt(ExternalLeaseRead):
    claim_request_id: UUID = Field(repr=False)
    claim_quantity: ResourceQuantity
    lease_token: LeaseToken = Field(repr=False)


class ExternalLeaseRevoke(APIModel):
    client_operation_id: UUID | None = Field(default=None, repr=False)


class ExternalLeaseRelease(ExternalLeaseRevoke):
    lease_token: LeaseToken = Field(repr=False)


class ExternalLeaseRenew(ExternalLeaseRelease):
    quantity: ResourceQuantity | SkipJsonSchema[None] = None
    lease_minutes: LeaseMinutes | SkipJsonSchema[None] = None

    @model_validator(mode="before")
    @classmethod
    def renewal_fields_are_nonnull(cls, data: object) -> object:
        return _reject_explicit_null(data, "lease_minutes", "quantity")


class ExternalLeaseReleased(APIModel):
    project_id: UUID
    resource_id: UUID
    lease_id: UUID
    released: bool
