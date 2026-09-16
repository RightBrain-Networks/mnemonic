"""Common predicates keep discovery and its diagnostics on identical scopes."""

from mnemonic_api.search_exploration_schemas import DATE_FIELDS, DateBounds, DiagnosticsMode


def date_conditions(filters: DateBounds, created, updated) -> list:
    conditions = []
    for name, column in (("created_after", created), ("created_before", created),
                         ("updated_after", updated), ("updated_before", updated)):
        value = getattr(filters, name)
        if value is not None:
            conditions.append(column >= value if name.endswith("after") else column < value)
    return conditions


def date_sql(filters: object, alias: str) -> tuple[list[str], dict]:
    """Fixed fields only; caller supplies the application-owned SQL alias."""
    clauses, parameters = [], {}
    for name in DATE_FIELDS:
        value = getattr(filters, name, None)
        if value is not None:
            column, operator = name.split("_")[0] + "_at", ">=" if name.endswith("after") else "<"
            clauses.append(f"{alias}.{column} {operator} :search_{name}")
            parameters[f"search_{name}"] = value
    return clauses, parameters


def wants_diagnostics(mode: DiagnosticsMode, query: str | None, total: int) -> bool:
    return bool(query and query.strip()) and (
        mode == "always" or (mode == "on_empty" and not total)
    )
