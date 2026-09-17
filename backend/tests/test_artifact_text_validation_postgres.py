"""Artifact text pins expose reviewed parameter names without caller values."""

import pytest

from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, upload

pytestmark = pytest.mark.postgres


def test_invalid_text_hash_names_the_pin_without_echoing_its_value(api, project, artifact_storage):
    artifact = upload(api, project, body=b"current artifact text")
    result = api.get(collection(project) + "/" + artifact["id"] + "/text", params={
        "expected_revision": 1, "expected_text_sha256": "PRIVATE_INVALID_PIN",
    })
    assert result.status_code == 422, result.text
    assert result.json()["detail"] == [{
        "type": "string_pattern_mismatch", "loc": ["query", "expected_text_sha256"],
        "msg": "String format is invalid.",
    }]
    assert "PRIVATE_INVALID_PIN" not in result.text
