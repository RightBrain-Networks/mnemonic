"""Agent operations on project-scoped, human-defined external resources."""

from typing import Annotated, Literal, Self, cast
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StrictInt, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from .api import MnemonicAPI, TransportEffect
from .lease_models import LeaseMinutesArgument, LeaseSettingsRead, lease_minutes_payload
from .models import RetainedClientName, RetainedSessionID, TitleEventText, UTCDateTime
from .phase12_models import Phase12Wire
from .response_validation import response_matches, validate_unique_rows
from .server import MUTATE, READ, LeaseTokenInput

Quantity = Annotated[StrictInt, Field(ge=1, le=4_294_967_295)]


class ExternalLease(Phase12Wire):
    id: UUID
    resource_id: UUID
    holder_client: RetainedClientName
    holder_session_id: RetainedSessionID
    quantity: Quantity
    acquired_at: UTCDateTime
    expires_at: UTCDateTime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("Lease expiry must follow acquisition.")
        return self


class ExternalLeaseClaim(ExternalLease):
    claim_request_id: UUID
    claim_quantity: Quantity
    lease_token: Annotated[str, Field(min_length=1, max_length=200, repr=False)]


class ExternalResource(LeaseSettingsRead):
    id: UUID
    project_id: UUID
    name: TitleEventText
    capacity: Quantity
    available: Annotated[StrictInt, Field(ge=0, le=4_294_967_295)]
    leases: list[ExternalLease]

    @model_validator(mode="after")
    def validate_reservations(self) -> Self:
        validate_unique_rows(self.leases, key=lambda item: item.id)
        validate_unique_rows(
            self.leases, key=lambda item: (item.holder_client, item.holder_session_id),
        )
        if any(lease.resource_id != self.id for lease in self.leases):
            raise ValueError("Lease belongs to a different external resource.")
        if self.available + sum(lease.quantity for lease in self.leases) != self.capacity:
            raise ValueError("External resource availability disagrees with active leases.")
        return self


class ExternalResourceList(Phase12Wire):
    project_id: UUID
    items: list[ExternalResource]

    @model_validator(mode="after")
    def validate_resources(self) -> Self:
        validate_unique_rows(self.items, key=lambda item: item.id)
        if any(item.project_id != self.project_id for item in self.items):
            raise ValueError("Resource belongs to a different project.")
        return self


class ExternalLeaseRelease(Phase12Wire):
    project_id: UUID
    resource_id: UUID
    lease_id: UUID
    released: Literal[True]


def register_external_lease_tools(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=READ)
    async def list_external_resources(project_id: UUID) -> ExternalResourceList:
        """Read available units, duration bounds and active reservations for every external resource in a project. Select a resource ID and check its available quantity and expiry times before claiming. Humans create and edit these resources under Resources > External Leases. A status read does not reserve capacity."""
        return cast(ExternalResourceList, await api.request(
            "GET", f"projects/{project_id}/external-resources",
            response_model=ExternalResourceList, effect=TransportEffect.SAFE_READ,
            expected_status_code=200, strict_wire_response=True,
            response_validator=response_matches(
                ExternalResourceList, lambda page: page.project_id == project_id,
            ),
        ))

    @server.tool(annotations=MUTATE)
    async def claim_external_lease(
        project_id: UUID, resource_id: UUID, holder_client: RetainedClientName,
        holder_session_id: RetainedSessionID, quantity: Quantity, claim_request_id: UUID,
        lease_minutes: LeaseMinutesArgument = MISSING,
    ) -> ExternalLeaseClaim:
        """Reserve available units using your client and native session identity. A session may hold one lease per resource. Omit lease_minutes to use the resource default or specify minutes within its minimum and maximum. Wait for a successful claim before using the resource. Retain the returned lease ID, private token and retry ID. On an uncertain outcome retry only the exact claim. A replay of an active lease returns its current quantity and expiry alongside the original claim_quantity. For an existing reservation use renew_external_lease to change quantity or time."""
        return cast(ExternalLeaseClaim, await api.request(
            "POST", f"projects/{project_id}/external-resources/{resource_id}/claim",
            payload={"holder_client": holder_client, "holder_session_id": holder_session_id,
                     "quantity": quantity, "claim_request_id": str(claim_request_id),
                     **lease_minutes_payload(lease_minutes)},
            response_model=ExternalLeaseClaim, effect=TransportEffect.LEASE_CLAIM,
            expected_status_code=200, strict_wire_response=True,
            response_validator=response_matches(ExternalLeaseClaim, lambda lease: (
                lease.resource_id == resource_id and lease.claim_quantity == quantity
                and lease.claim_request_id == claim_request_id
                and lease.holder_client == holder_client.strip()
                and lease.holder_session_id == holder_session_id.strip()
            )),
        ))

    @server.tool(annotations=MUTATE)
    async def renew_external_lease(
        project_id: UUID, resource_id: UUID, lease_id: UUID, lease_token: LeaseTokenInput,
        client_operation_id: UUID, quantity: Quantity | MISSING = MISSING,
        lease_minutes: LeaseMinutesArgument = MISSING,
    ) -> ExternalLease:
        """Renew an active reservation using its private token. Expiry is calculated from server time using lease_minutes or the current resource default. To keep the held quantity omit quantity. To resize, pass the new total: an agent holding 8 requests quantity=10 to acquire 2 more, or quantity=5 to return 3. Capacity must cover any increase. Both changes succeed together. Release the lease to return all units."""
        return cast(ExternalLease, await api.request(
            "POST", f"projects/{project_id}/external-resources/{resource_id}/leases/{lease_id}/renew",
            payload={"lease_token": lease_token.get_secret_value(),
                     "client_operation_id": str(client_operation_id),
                     **({"quantity": quantity} if quantity is not MISSING else {}),
                     **lease_minutes_payload(lease_minutes)},
            response_model=ExternalLease, effect=TransportEffect.RECEIPT_PROTECTED_WRITE,
            expected_status_code=200, response_validator=response_matches(
                ExternalLease, lambda lease: lease.resource_id == resource_id and lease.id == lease_id
                and (quantity is MISSING or lease.quantity == quantity),
            ),
        ))

    @server.tool(annotations=MUTATE)
    async def release_external_lease(
        project_id: UUID, resource_id: UUID, lease_id: UUID, lease_token: LeaseTokenInput,
        client_operation_id: UUID,
    ) -> ExternalLeaseRelease:
        """Return every unit held by this external lease as soon as you finish using the resource. Supply its ID and private token. For a partial return use renew_external_lease with the smaller remaining total. Expiry or manual release by a dashboard user also ends the reservation."""
        return cast(ExternalLeaseRelease, await api.request(
            "POST", f"projects/{project_id}/external-resources/{resource_id}/leases/{lease_id}/release",
            payload={"lease_token": lease_token.get_secret_value(),
                     "client_operation_id": str(client_operation_id)},
            response_model=ExternalLeaseRelease, effect=TransportEffect.RECEIPT_PROTECTED_WRITE,
            expected_status_code=200, response_validator=response_matches(
                ExternalLeaseRelease, lambda receipt: (
                    receipt.project_id == project_id and receipt.resource_id == resource_id
                    and receipt.lease_id == lease_id
                ),
            ),
        ))
