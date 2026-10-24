"""Cold-review status paths do not load decision context."""

from types import SimpleNamespace
from uuid import uuid4

from mnemonic_api.services import decision_scope_notices


def test_code_review_holder_never_gets_a_decision_scope_notice(monkeypatch):
    monkeypatch.setattr(decision_scope_notices, "active_holder",
                        lambda _database, _work: SimpleNamespace(purpose="code_review"))
    # No database methods exist: suppression happens before reading the decision scope.
    assert decision_scope_notices.pending_scope_notice(object(), uuid4()) is None
