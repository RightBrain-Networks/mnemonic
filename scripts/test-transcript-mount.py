"""Exercise the production Compose transcript mount with private synthetic files.

Run with Python 3.14 and Docker from any directory. Builds an isolated API image;
never starts the API server, connects to a database, or mounts real transcripts.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SETTINGS = {
    "MNEMONIC_TRANSCRIPT_SOURCE_DIR": "transcript-source-disabled",
    "MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR": "codex-transcript-source-disabled",
    "MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR": "codex-archived-transcript-source-disabled",
}
PROBE = '''
import errno
import os
from pathlib import Path
from uuid import uuid4
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.transcript_discovery import discover_transcripts
from mnemonic_api.transcript_storage import check_transcript_source, read_transcript
settings = Settings()
for configured in settings.transcript_source_dirs:
    check_transcript_source(configured, settings.transcript_allowed_roots)
assert settings.transcript_allowed_roots == settings.transcript_source_dirs
root = Path(os.environ["PROBE_ROOT"])
assert root in settings.transcript_source_dirs
assert os.geteuid() == int(os.environ["EXPECTED_UID"]) > 0
assert os.getegid() == int(os.environ["EXPECTED_GID"]) > 0
source = root / os.environ["PROBE_SOURCE"]
assert read_transcript(str(source), [root], 1024) == b"synthetic transcript\\n"
scan = discover_transcripts(str(root), [root])
assert str(source) in scan.paths
try:
    source.open("ab")
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError("Transcript mount is writable")
try:
    read_transcript("/etc/passwd", [root], 1024)
except ExtractionError as error:
    assert str(error) == "transcript_path_not_allowed"
else:
    raise AssertionError("Outside-root source was accepted")
try:
    read_transcript(str(root / "linked.jsonl"), [root], 1024)
except ExtractionError as error:
    assert str(error) == "transcript_io_error"
else:
    raise AssertionError("Symlink was followed")
assert not (root.parent / "credentials.json").exists()
assert not (root.parent / "auth.json").exists()
store = ArtifactStorage(settings.artifact_root, max_bytes=1024)
staged = store.stage(uuid4(), uuid4(), "sample.txt", [b"private artifact"])
store.publish(staged)
assert (settings.artifact_root / staged.relative_path).read_bytes() == b"private artifact"
assert (settings.artifact_root / staged.relative_path).stat().st_uid == os.geteuid()
assert settings.transcript_search_max_bytes == 1048576
index = ArtifactSearchIndex(settings.transcript_index_dir)
try:
    result = index.search("mount-probe", lambda: [SearchDocument("one", "probe", "needle")],
                          query="needle", fulltext=True, count=1)
    assert len(result.hits) == 1
    assert (settings.transcript_index_dir / "snapshot" / "meta.json").is_file()
finally:
    index.close()
print("PASS: private source read/discovery, containment, artifact write and configured disk index")
'''
DENIED_PROBE = '''
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.transcript_storage import read_transcript
for root in Settings().transcript_allowed_roots:
    try:
        read_transcript(str(root / "existing.jsonl"), [root], 1024)
    except ExtractionError as error:
        assert str(error) == "transcript_io_error"
    else:
        raise AssertionError("Wrong UID read an owner-only transcript")
print("PASS: mismatched service UID reproduces the permission failure")
'''


def command(args: list[str], *, env: dict[str, str], content: str | None = None,
            succeeds: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, env=env, input=content, text=True, capture_output=True, check=False,
    )
    if (result.returncode == 0) != succeeds:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result


def check_config(
    compose: list[str], env: dict[str, str], sources: dict[str, Path], *, tls: bool,
) -> None:
    services = json.loads(command([*compose, "config", "--format", "json"], env=env).stdout)[
        "services"
    ]
    api = services["api"]
    assert api["build"]["args"]["MNEMONIC_API_UID"] == env["MNEMONIC_API_UID"]
    assert api["build"]["args"]["MNEMONIC_API_GID"] == env["MNEMONIC_API_GID"]
    assert json.loads(api["environment"]["MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS"]) == []
    for name, source in sources.items():
        assert api["environment"][name] == str(source)
        mounts = [m for m in api["volumes"] if m["target"] == str(source)]
        assert len(mounts) == 1
        assert mounts[0]["source"] == str(source) and mounts[0]["read_only"]
        assert not mounts[0]["bind"].get("create_host_path", False)
    index_root = env["MNEMONIC_TRANSCRIPT_INDEX_DIR"]
    assert api["environment"]["MNEMONIC_TRANSCRIPT_INDEX_DIR"] == index_root
    assert int(api["environment"]["MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES"]) == 1048576
    index_mount = next(m for m in api["volumes"] if m["target"] == index_root)
    assert index_mount["source"] == index_root and not index_mount.get("read_only", False)
    assert not index_mount["bind"].get("create_host_path", False)
    private_mounts = {*map(str, sources.values()), index_root}
    for name, service in services.items():
        if name != "api":
            assert all(m["source"] not in private_mounts for m in service.get("volumes", []))
    origins = api["environment"]["MNEMONIC_DASHBOARD_ORIGINS"]
    assert ("https://transcript-test.invalid" in origins) == tls


def exercise_sources(compose: list[str], env: dict[str, str], sources: dict[str, Path]) -> None:
    run = [*compose, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python"]
    other_uid = "10001" if os.getuid() != 10001 else "10002"
    print(command([*run, "--user", f"{other_uid}:{other_uid}", "api", "-"],
                  env=env, content=DENIED_PROBE).stdout.strip())
    filenames = (
        "session/subagents/workflows/wf-synthetic/agent.jsonl",
        "2026/09/12/rollout-agent.jsonl", "rollout-archived.jsonl",
    )
    for (name, source), filename in zip(sources.items(), filenames, strict=True):
        for probe_filename in ("existing.jsonl", filename):
            path = source / probe_filename
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(b"synthetic transcript\n")
            path.chmod(0o600)
            args = [*run, "-e", f"EXPECTED_UID={os.getuid()}", "-e", f"EXPECTED_GID={os.getgid()}",
                    "-e", f"PROBE_ROOT={source}", "-e", f"PROBE_SOURCE={probe_filename}", "api", "-"]
            print(command(args, env=env, content=PROBE).stdout.strip())
        missing = source / "does-not-exist"
        result = command([*run, "api", "-c", "raise AssertionError('missing source was mounted')"],
                         env={**env, name: str(missing)}, succeeds=False)
        assert "bind source path does not exist" in result.stderr, result.stderr
        assert not missing.exists()
    print("PASS: every missing bind source is rejected without creating it")


def exercise_disabled(compose: list[str], env: dict[str, str], sources: dict[str, Path]) -> None:
    disabled_env = {key: value for key, value in env.items() if key not in SOURCE_SETTINGS}
    probe = """
from pathlib import Path
from mnemonic_api.config import Settings
settings = Settings()
assert settings.transcript_source_dirs == []
assert settings.transcript_allowed_roots == []
for name in ("transcript-source-disabled", "codex-transcript-source-disabled",
             "codex-archived-transcript-source-disabled"):
    placeholder = Path("/var/lib/mnemonic") / name
    assert sorted(path.name for path in placeholder.iterdir()) == [".keep"]
print("PASS: no configured sources leaves transcript access disabled")
"""
    for configured in (disabled_env, {**disabled_env, **dict.fromkeys(SOURCE_SETTINGS, "")}):
        api = json.loads(command([*compose, "config", "--format", "json"], env=configured)
                         .stdout)["services"]["api"]
        assert all(m["source"] not in set(map(str, sources.values())) for m in api["volumes"])
        for target in SOURCE_SETTINGS.values():
            mount = next(m for m in api["volumes"] if m["target"] == f"/var/lib/mnemonic/{target}")
            assert mount["source"] == str(ROOT / "deploy/empty-transcripts")
            assert mount["read_only"] and not mount["bind"].get("create_host_path", False)
        print(command([*compose, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python",
                       "api", "-"], env=configured, content=probe).stdout.strip())


def exercise(compose: list[str], env: dict[str, str], sources: dict[str, Path]) -> None:
    command([*compose, "build", "api"], env=env)
    base = [*compose, "-f", str(ROOT / "compose.yaml")]
    variants = [
        ("base only", base, False),
        ("explicit base and TLS", [*base, "-f", str(ROOT / "compose.tls.yaml")], True),
        ("saved base, TLS and transcript overlay", compose, True),
    ]
    for name, selected, tls in variants:
        check_config(selected, env, sources, tls=tls)
        exercise_sources(selected, env, sources)
        exercise_disabled(selected, env, sources)
        print(f"PASS: {name}")


def main() -> None:
    if os.getuid() == 0 or os.getgid() == 0:
        raise SystemExit("Run as an unprivileged Docker-capable host user.")
    project = "mnemonic-transcript-mount-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="mnemonic-transcript-mount-") as temporary:
        directory = Path(temporary)
        private_home = directory / 'private "quoted" home'
        sources = dict(zip(SOURCE_SETTINGS, (
            private_home / ".claude" / "projects", private_home / ".codex" / "sessions",
            private_home / ".codex" / "archived_sessions",
        ), strict=True))
        for source in sources.values():
            source.mkdir(mode=0o700, parents=True)
            (source.parent / "credentials.json").write_text("synthetic unmounted credentials")
            (source.parent / "auth.json").write_text("synthetic unmounted Codex credentials")
            (source / "existing.jsonl").write_bytes(b"synthetic transcript\n")
            (source / "existing.jsonl").chmod(0o600)
            (source / "linked.jsonl").symlink_to(source / "existing.jsonl")
        for name in ("artifacts", "backups", "transcript-index", "prompts"):
            (directory / name).mkdir(mode=0o700)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MNEMONIC_", "COMPOSE_", "POSTGRES_"))}
        env.update({
            "COMPOSE_FILE": ":".join(
                str(ROOT / name)
                for name in ("compose.yaml", "compose.tls.yaml", "compose.transcripts.yaml")
            ),
            "MNEMONIC_API_UID": str(os.getuid()), "MNEMONIC_API_GID": str(os.getgid()),
            **{name: str(source) for name, source in sources.items()},
            "MNEMONIC_ARTIFACT_DIR": str(directory / "artifacts"),
            "MNEMONIC_PROMPT_DIR": str(directory / "prompts"),
            "MNEMONIC_TRANSCRIPT_INDEX_DIR": str(directory / "transcript-index"),
            "MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES": "1048576",
            "MNEMONIC_BACKUP_DIR": str(directory / "backups"),
            "MNEMONIC_TLS_HOST": "transcript-test.invalid",
            "POSTGRES_PASSWORD": "synthetic-mount-test",
            "MNEMONIC_API_KEY": "synthetic-mount-test-key-long-enough",
            "MNEMONIC_BACKUP_TOKEN": "synthetic-mount-test-backup-long-enough",
        })
        env_file = directory / "compose.env"
        env_file.write_text("COMPOSE_FILE=" + json.dumps(env.pop("COMPOSE_FILE")) + "\n")
        compose = ["docker", "compose", "--env-file", str(env_file), "-p", project]
        try:
            exercise(compose, env, sources)
        finally:
            command([*compose, "down", "--remove-orphans", "--rmi", "local"], env=env)


if __name__ == "__main__":
    main()
