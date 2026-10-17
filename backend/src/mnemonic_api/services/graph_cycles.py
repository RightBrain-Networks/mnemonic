"""Iterative graph analysis; no recursion or path-depth cutoff."""

import hashlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from mnemonic_api.precedence_schemas import MAX_CYCLE_STEPS, DependencyIssue, PrecedenceStep


@dataclass(frozen=True)
class Edge:
    id: UUID | None
    type: str
    source: UUID
    target: UUID

    @property
    def before(self) -> UUID:
        return self.target if self.type == "parent-child" else self.source

    @property
    def after(self) -> UUID:
        return self.source if self.type == "parent-child" else self.target

    def step(self) -> PrecedenceStep:
        return PrecedenceStep(
            relationship_id=self.id,
            relationship_type="parent-child" if self.type == "parent-child" else "blocks",
            source_work_item_id=self.source,
            target_work_item_id=self.target,
        )


def adjacency(edges: Iterable[Edge]) -> dict[UUID, list[Edge]]:
    result: dict[UUID, list[Edge]] = {}
    for edge in sorted(edges, key=lambda e: (e.before, e.after, e.type, str(e.id))):
        result.setdefault(edge.before, []).append(edge)
        result.setdefault(edge.after, [])
    return result


def path(graph: dict[UUID, list[Edge]], start: UUID, end: UUID) -> list[Edge] | None:
    """Find one shortest witness using a visited set, even on retained bad graphs."""
    visited: dict[UUID, Edge | None] = {start: None}
    pending = deque([start])
    while pending:
        current = pending.popleft()
        if current == end:
            return _unwind(visited, end)
        for edge in graph.get(current, []):
            if edge.after not in visited:
                visited[edge.after] = edge
                pending.append(edge.after)
    return None


def _unwind(visited: dict[UUID, Edge | None], end: UUID) -> list[Edge]:
    result: list[Edge] = []
    edge = visited[end]
    while edge is not None:
        result.append(edge)
        edge = visited[edge.before]
    return list(reversed(result))


def closing_cycle(edges: Iterable[Edge], proposed: Edge) -> list[Edge] | None:
    found = path(adjacency(edges), proposed.after, proposed.before)
    return None if found is None else [proposed, *found]


def _finish_order(graph: dict[UUID, list[Edge]]) -> list[UUID]:
    visited: set[UUID] = set()
    result: list[UUID] = []
    for root in graph:
        if root in visited:
            continue
        pending = [(root, False)]
        while pending:
            node, leaving = pending.pop()
            if leaving:
                result.append(node)
            elif node not in visited:
                visited.add(node)
                pending.append((node, True))
                pending.extend((edge.after, False) for edge in reversed(graph[node]))
    return result


def components(graph: dict[UUID, list[Edge]]) -> list[set[UUID]]:
    """Kosaraju components in O(V+E), without enumerating individual cycles."""
    reverse: dict[UUID, list[UUID]] = {node: [] for node in graph}
    for node, edges in graph.items():
        for edge in edges:
            reverse[edge.after].append(node)
    visited: set[UUID] = set()
    result: list[set[UUID]] = []
    for root in reversed(_finish_order(graph)):
        if root not in visited:
            component = _collect(reverse, root, visited)
            if len(component) > 1 or any(edge.after == root for edge in graph[root]):
                result.append(component)
    return result


def _collect(reverse: dict[UUID, list[UUID]], root: UUID, visited: set[UUID]) -> set[UUID]:
    found: set[UUID] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            found.add(node)
            pending.extend(reverse[node])
    return found


def witness(graph: dict[UUID, list[Edge]], component: set[UUID]) -> list[Edge]:
    root = min(component)
    first = next(edge for edge in graph[root] if edge.after in component)
    remainder = path(graph, first.after, root)
    assert remainder is not None
    return [first, *remainder]


def issue_for_cycle(cycle: list[Edge], *, planning: bool = False) -> DependencyIssue:
    fingerprint = "\n".join(f"{e.id}:{e.type}:{e.source}:{e.target}" for e in cycle)
    return DependencyIssue(
        kind="planning-cycle" if planning else "deadlock",
        role="cycle_member",
        cycle_id=hashlib.sha256(fingerprint.encode()).hexdigest(),
        cycle_length=len(cycle),
        cycle=[edge.step() for edge in cycle[:MAX_CYCLE_STEPS]],
    )


def cycle_issues(edges: Iterable[Edge], *, planning: bool = False) -> dict[UUID, DependencyIssue]:
    graph = adjacency(edges)
    found: dict[UUID, DependencyIssue] = {}
    pending: deque[UUID] = deque()
    for component in sorted(components(graph), key=min):
        issue = issue_for_cycle(witness(graph, component), planning=planning)
        for member in sorted(component):
            found[member] = issue
            pending.append(member)
    while pending:
        node = pending.popleft()
        for edge in graph[node]:
            if edge.after not in found:
                found[edge.after] = found[node].model_copy(update={"role": "depends_on_cycle"})
                pending.append(edge.after)
    return found
