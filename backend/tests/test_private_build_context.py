"""Default private filesystem binds must never enter the backend build context."""

from pathlib import Path


def test_private_runtime_data_is_excluded_from_docker_build_context():
    root = Path(__file__).parents[2]
    exclusions = set((root / ".dockerignore").read_text().splitlines())
    private_paths = {"artifacts", "backups", "transcripts", "transcript-index",
                     "prompts/runtime", ".local"}
    assert private_paths <= exclusions
