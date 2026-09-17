"""A bounded explicit project selection shares one search corpus and snapshot."""

from uuid import UUID

type ProjectSelection = UUID | tuple[UUID, ...]


def selected_project_ids(selection: ProjectSelection) -> tuple[UUID, ...]:
    return (selection,) if isinstance(selection, UUID) else selection


def project_scope(column, selection: ProjectSelection):
    identities = selected_project_ids(selection)
    return column == identities[0] if len(identities) == 1 else column.in_(identities)
