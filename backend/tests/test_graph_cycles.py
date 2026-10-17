"""Cycle witnesses and propagation distinguish loops from ordinary pending tails."""

from uuid import UUID, uuid4

from mnemonic_api.services.graph_cycles import Edge, closing_cycle, cycle_issues


def edge(source, target, kind="blocks"):
    return Edge(uuid4(), kind, UUID(int=source), UUID(int=target))


def test_composed_cycle_and_acyclic_upstream_tail():
    edges = [
        edge(1, 5, "parent-child"),
        edge(1, 2),
        edge(2, 3),
        edge(3, 4),
        edge(4, 5),
        edge(6, 7),
        edge(7, 2),
        edge(5, 8),
    ]
    issues = cycle_issues(edges)
    assert set(issues) == {UUID(int=n) for n in [1, 2, 3, 4, 5, 8]}
    assert issues[UUID(int=8)].role == "depends_on_cycle"
    assert all(issues[UUID(int=n)].role == "cycle_member" for n in range(1, 6))
    assert issues[UUID(int=1)].cycle_length == 5
    assert any(step.relationship_type == "parent-child" for step in issues[UUID(int=1)].cycle)


def test_long_dag_and_cycle_beyond_recursion_limit():
    edges = [edge(n, n + 1) for n in range(1, 2200)]
    assert cycle_issues(edges) == {}
    cycle = closing_cycle(edges, edge(2200, 1))
    assert cycle is not None and len(cycle) == 2200
    issues = cycle_issues([*edges, edge(2200, 1)])
    assert len(issues) == 2200
    assert issues[UUID(int=1)].cycle_length == 2200
    assert len(issues[UUID(int=1)].cycle) == 32


def test_disjoint_cycles_keep_membership_and_terminate_on_existing_bad_data():
    edges = [edge(1, 2), edge(2, 1), edge(2, 3), edge(3, 4), edge(4, 3), edge(9, 9)]
    issues = cycle_issues(edges)
    assert issues[UUID(int=3)].role == "cycle_member"
    assert issues[UUID(int=1)].cycle_id != issues[UUID(int=3)].cycle_id
    assert issues[UUID(int=9)].cycle_length == 1
    assert closing_cycle(edges, edge(4, 10)) is None
