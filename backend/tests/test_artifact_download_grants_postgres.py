"""Download capabilities retain scope, approval policy and one-use consumption."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mnemonic_api.models import ArtifactDownloadCapability

from .test_artifact_links_sensitive_postgres import update
from .test_artifact_sensitive_postgres import ACTOR, _token
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


def authorize(api, path, **changes):
    return api.post(path + "/download-grants", json={"expected_revision": 1, **ACTOR, **changes})


def redeem(api, path, grant):
    return api.get(path + "/content", params={"expected_revision": grant["revision"]},
                   headers={"X-Artifact-Download-Grant": grant["download_token"]})


def test_download_grant_is_single_use_under_concurrency(api, project, artifact_storage):
    artifact = upload(api, project, body=b"download bytes")
    path = collection(project) + "/" + artifact["id"]
    issued = authorize(api, path)
    assert issued.status_code == 200, issued.text
    grant = issued.json()
    assert grant["sha256"] == artifact["sha256"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem(api, path, grant), range(2)))
    assert sorted(result.status_code for result in results) == [200, 401]
    downloaded = next(result.content for result in results if result.status_code == 200)
    assert downloaded == b"download bytes"
    with api.app.state.session_factory() as database:
        stored = database.scalar(select(ArtifactDownloadCapability))
        assert stored.token_hash == hashlib.sha256(grant["download_token"].encode()).hexdigest()
        assert stored.consumed_at is not None
    assert grant["download_token"] not in api.get(path + "/history").text


def test_sensitive_grants_require_new_human_approval_each_time(api, project, artifact_storage):
    artifact = upload(api, project, metadata={"sensitive": True})
    path = collection(project) + "/" + artifact["id"]
    token = _token(authorize(api, path))
    grant = authorize(api, path, approval_token=token, human_approved=True).json()
    assert redeem(api, path, grant).status_code == 200
    assert redeem(api, path, grant).status_code == 401
    _token(authorize(api, path, approval_token=token, human_approved=True))
    _token(authorize(api, path))
    actions = [row["action"] for row in api.get(path + "/history").json()["audit"]["items"]]
    assert actions.count("approval_granted") == 1 and actions.count("sensitive_downloaded") == 1


@pytest.mark.parametrize(
    "changed", ["artifact", "project", "revision", "expired", "token", "deleted"],
)
def test_download_grants_cannot_cross_scope(api, project, artifact_storage, changed):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"]
    grant = authorize(api, path).json()
    if changed == "artifact":
        other = upload(api, project, filename="other.txt")
        path = collection(project) + "/" + other["id"]
    elif changed == "project":
        other_project = api.post("/api/v1/projects", json={"name": "Other"}).json()
        path = collection(other_project) + "/" + artifact["id"]
    elif changed == "revision":
        assert update(api, project, artifact, sensitive=True).status_code == 200
        grant["revision"] = 2
    elif changed == "deleted":
        assert api.delete(path, headers=headers(revision=1)).status_code == 200
    elif changed == "expired":
        with api.app.state.session_factory() as database:
            stored = database.scalar(select(ArtifactDownloadCapability))
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            database.commit()
    else:
        grant["download_token"] = "x" * 43
    result = redeem(api, path, grant)
    assert result.status_code in {401, 404, 410}, result.text
