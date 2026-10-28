"""Validate native image receipts, merge candidates and promote tested release digests."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

IMAGES = ("api", "mcp", "web", "tika")
PREFIX = "ghcr.io/rightbrain-networks/mnemonic-"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def validate_identity(commit: str, version: str) -> None:
    if not COMMIT.fullmatch(commit) or not VERSION.fullmatch(version):
        raise ValueError("Expected a full commit SHA and stable MAJOR.MINOR.PATCH version")


def image_digests(values: dict) -> dict[str, str]:
    if set(values) != set(IMAGES) or any(not DIGEST.fullmatch(v) for v in values.values()):
        raise ValueError("Expected exactly four valid SHA-256 image digests")
    return values


def record(output: Path, architecture: str, commit: str, version: str) -> None:
    validate_identity(commit, version)
    values = {name: os.environ[f"DIGEST_{name.upper()}"] for name in IMAGES}
    result = {"commit": commit, "version": version, "platform": f"linux/{architecture}",
              "images": image_digests(values)}
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{architecture}.json").write_text(json.dumps(result, indent=2) + "\n")


def load_native(directory: Path, commit: str, version: str) -> dict[str, dict[str, str]]:
    validate_identity(commit, version)
    result = {}
    paths = sorted(directory.glob("*.json"))
    if len(paths) != 2:
        raise ValueError("Expected exactly two native image receipts")
    for path in paths:
        value = json.loads(path.read_text())
        platform = value["platform"]
        if value["commit"] != commit or value["version"] != version:
            raise ValueError("Native receipt belongs to another commit or version")
        if platform not in {"linux/amd64", "linux/arm64"} or platform in result:
            raise ValueError("Native receipts must cover amd64 and arm64 exactly once")
        result[platform] = image_digests(value["images"])
    return result


def inspect(reference: str) -> dict:
    return json.loads(run("docker", "buildx", "imagetools", "inspect", reference,
                          "--format", "{{json .Manifest}}"))


def verify_index(document: dict) -> str:
    native = {m.get("platform", {}).get("architecture"): m.get("digest")
              for m in document.get("manifests", [])
              if m.get("platform", {}).get("os") == "linux"}
    if not {"amd64", "arm64"}.issubset(native):
        raise ValueError("Published image is missing a required native platform")
    attested = {m.get("annotations", {}).get("vnd.docker.reference.digest")
                for m in document.get("manifests", [])
                if m.get("annotations", {}).get("vnd.docker.reference.type")
                == "attestation-manifest"}
    if not {native["amd64"], native["arm64"]}.issubset(attested):
        raise ValueError("Published image is missing native BuildKit attestations")
    digest = document.get("digest", "")
    if not DIGEST.fullmatch(digest):
        raise ValueError("Registry did not return a valid index digest")
    return digest


def write_outputs(images: dict[str, str]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a") as output:
            for name, digest in images.items():
                output.write(f"{name}={digest}\n")


def merge(
    directory: Path, output: Path, commit: str, version: str, edge: bool, candidate_tag: str,
) -> None:
    validate_identity(commit, version)
    if not re.fullmatch(rf"sha-{commit}-[0-9]+-[0-9]+", candidate_tag):
        raise ValueError("Candidate tag must pin this commit, workflow run and attempt")
    native = load_native(directory, commit, version)
    images = {}
    for name in IMAGES:
        reference = f"{PREFIX}{name}"
        candidate = f"{reference}:{candidate_tag}"
        sources = [f"{reference}@{native[p][name]}" for p in sorted(native)]
        run("docker", "buildx", "imagetools", "create", "--tag", candidate, *sources)
        images[name] = verify_index(inspect(candidate))
        if edge:
            run("docker", "buildx", "imagetools", "create", "--tag", f"{reference}:edge",
                "--tag", f"{reference}:sha-{commit[:12]}", f"{reference}@{images[name]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"commit": commit, "version": version,
               "candidate_tag": candidate_tag, "images": images}
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    write_outputs(images)


def load_candidate(receipt: Path, commit: str, version: str) -> dict:
    validate_identity(commit, version)
    value = json.loads(receipt.read_text())
    if value["commit"] != commit or value["version"] != version:
        raise ValueError("Candidate receipt belongs to another release")
    if not re.fullmatch(rf"sha-{commit}-[0-9]+-[0-9]+", value.get("candidate_tag", "")):
        raise ValueError("Candidate receipt must identify its original build run and attempt")
    image_digests(value["images"])
    return value


def candidate(receipt: Path, commit: str, version: str) -> None:
    tag = load_candidate(receipt, commit, version)["candidate_tag"]
    if destination := os.environ.get("GITHUB_OUTPUT"):
        with Path(destination).open("a") as output:
            output.write(f"tag={tag}\n")
    print(tag)


def promote(receipt: Path, commit: str, version: str) -> None:
    value = load_candidate(receipt, commit, version)
    images = value["images"]
    minor = ".".join(version.split(".")[:2])
    # Inspect every immutable release destination before moving any aliases.
    for name, digest in images.items():
        reference = f"{PREFIX}{name}"
        if verify_index(inspect(f"{reference}@{digest}")) != digest:
            raise ValueError("Candidate digest changed")
        # Never replace an existing exact release tag with different bytes.
        existing = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", f"{reference}:{version}",
             "--format", "{{json .Manifest}}"], capture_output=True, text=True,
        )
        if existing.returncode == 0 and verify_index(json.loads(existing.stdout)) != digest:
            raise ValueError(f"Refusing to overwrite published release: {reference}:{version}")
        if existing.returncode and not any(text in existing.stderr.lower()
                                          for text in ("not found", "manifest unknown")):
            raise RuntimeError("Could not establish whether the release tag already exists")
    for name, digest in images.items():
        reference = f"{PREFIX}{name}"
        run("docker", "buildx", "imagetools", "create",
            "--tag", f"{reference}:{version}", "--tag", f"{reference}:{minor}",
            "--tag", f"{reference}:latest", f"{reference}@{digest}")
        if verify_index(inspect(f"{reference}:{version}")) != digest:
            raise ValueError("Promoted image does not match tested candidate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("record", "merge", "candidate", "promote"):
        item = sub.add_parser(name)
        item.add_argument("--commit", required=True)
        item.add_argument("--version", required=True)
        if name in {"record", "merge"}:
            item.add_argument("--output", type=Path, required=True)
        if name == "record":
            item.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
        if name == "merge":
            item.add_argument("--directory", type=Path, required=True)
            item.add_argument("--edge", action="store_true")
            item.add_argument("--candidate-tag", required=True)
        if name in {"candidate", "promote"}:
            item.add_argument("--receipt", type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    {"record": record, "merge": merge, "candidate": candidate, "promote": promote}[command](**args)


if __name__ == "__main__":
    main()
