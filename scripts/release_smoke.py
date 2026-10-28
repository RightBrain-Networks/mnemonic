"""Install a release bundle and optionally upgrade seeded data in an isolated stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from uuid import uuid4


def unpack(source: Path, destination: Path) -> Path:
    if source.is_dir():
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
            ".git", ".env", ".local", ".venv", "node_modules", "__pycache__"))
    else:
        destination.mkdir()
        with tarfile.open(source) as archive:
            archive.extractall(destination, filter="data")
    roots = list(destination.glob("*/compose.yaml"))
    root = destination if (destination / "compose.yaml").is_file() else roots[0].parent
    if not (root / ".env.example").is_file():
        raise RuntimeError("Release bundle lacks .env.example")
    return root


def free_port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def private_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(value)


def require_isolation(config: dict, project: str, root: Path) -> None:
    for group in ("volumes", "networks"):
        for item in config.get(group, {}).values():
            if (item.get("external") or item.get("driver_opts")
                    or not item.get("name", "").startswith(project + "_")):
                raise RuntimeError("Smoke test requires disposable project-owned storage/networks")
    for service in config["services"].values():
        if service.get("privileged") or service.get("network_mode") == "host":
            raise RuntimeError("Smoke test refuses privileged or host-network services")
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                source = Path(mount["source"]).resolve()
                if not mount.get("read_only") or not source.is_relative_to(root.resolve()):
                    raise RuntimeError("Smoke test bind mounts must be read-only bundle content")


class Stack:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.project = f"mnemonic-release-smoke-{uuid4().hex[:12]}"
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(("MNEMONIC_", "COMPOSE_"))
                            and key != "POSTGRES_PASSWORD"}
        self.ports = {name: free_port() for name in ("api", "mcp", "web")}
        if len(set(self.ports.values())) != 3:
            raise RuntimeError("Disposable ports collided; rerun the smoke test")
        self.config = scratch / "stack.json"
        self.key = ""

    def command(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose", "-p", self.project, "-f", str(self.config), *args],
            env=self.environment, check=True, **kwargs,
        )

    def setup(self, root: Path, image_tag: str, local: bool) -> None:
        setup = root / "setup.py"
        if not setup.is_file():
            setup = root / "scripts/setup.py"
        image = "mnemonic-api:local" if local else (
            f"ghcr.io/rightbrain-networks/mnemonic-api:{image_tag}")
        subprocess.run([
            "docker", "run", "--rm", "--pull", "never" if local else "always",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", f"type=bind,source={root},target=/release",
            "--entrypoint", "python", image, f"/release/{setup.relative_to(root)}",
        ], env=self.environment, check=True)
        env_file = root / ".env"
        info = env_file.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_uid, info.st_gid) != (os.getuid(), os.getgid())):
            raise RuntimeError("Container setup must create caller-owned regular 0600 settings")
        print("PASS: containerized setup retains caller-owned 0600 settings without host sudo")

    def configure(self, root: Path, image_tag: str, local: bool, settings: str | None) -> str:
        env_file = root / ".env"
        if settings is not None:
            private_write(env_file, settings)
        self.setup(root, image_tag, local)
        values = env_file.read_text()
        if settings is not None and values != settings:
            raise RuntimeError("Upgrade setup changed the existing private settings")
        self.key = next(line.split("=", 1)[1] for line in values.splitlines()
                        if line.startswith("MNEMONIC_API_KEY="))
        environment = {**self.environment, "MNEMONIC_VERSION": image_tag,
                       "MNEMONIC_DASHBOARD_ORIGINS":
                           f"http://127.0.0.1:{self.ports['web']}"}
        result = subprocess.run(
            ["docker", "compose", "-p", self.project, "--project-directory", str(root),
             "--env-file", str(env_file), "-f", str(root / "compose.yaml"),
             "config", "--format", "json"],
            env=environment, check=True, capture_output=True, text=True,
        )
        config = json.loads(result.stdout)
        require_isolation(config, self.project, root)
        for service, port in self.ports.items():
            target = {"api": 8000, "mcp": 8001, "web": 3000}[service]
            config["services"][service]["ports"] = [
                {"target": target, "published": str(port), "host_ip": "127.0.0.1"}]
        for name, service in config["services"].items():
            if "build" in service:
                raise RuntimeError("Release bundle unexpectedly requires a source build")
            if local and name in {"api", "worker", "init-storage", "migrate", "mcp", "web", "tika"}:
                image = "api" if name in {"worker", "init-storage", "migrate"} else name
                service["image"] = f"mnemonic-{image}:local"
                service["pull_policy"] = "never"
        private_write(self.config, json.dumps(config))
        return values

    def request(self, path: str, data: dict | None = None) -> dict:
        payload = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.ports['api']}{path}", data=payload,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def seed(self) -> tuple[str, str, str]:
        project = self.request("/api/v1/projects", {"name": "Release upgrade fixture"})
        project_id = project["id"]
        self.request(f"/api/v1/projects/{project_id}/tags")
        work = self.request(f"/api/v1/projects/{project_id}/work-items", {
            "title": "Preserve release smoke evidence", "summary": "Disposable upgrade fixture",
            "tags": None, "discovered_from_work_item_id": None,
            "client_operation_id": str(uuid4()),
            "initial_checkpoint": {"prompt": "Synthetic release test; no real work or authority.",
                                   "source_client": "release-smoke",
                                   "source_session_id": self.project},
        })
        work_id = work["work_item"]["id"]
        body = b"Mnemonic release upgrade artifact\n"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.ports['api']}/api/v1/projects/{project_id}/artifacts",
            method="POST", data=body,
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/octet-stream",
                     "X-Client-Operation-ID": str(uuid4()),
                     "X-Artifact-Metadata": json.dumps({"filename": "upgrade-proof.txt",
                         "tags": None, "agent_session_id": self.project,
                         "actor_client": "release-smoke", "work_item_id": work_id})},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            artifact = json.load(response)
        return project_id, work_id, artifact["id"]

    def verify(self, version: str, evidence: tuple[str, str, str]) -> None:
        if self.request("/openapi.json")["info"]["version"] != version:
            raise RuntimeError("Running API version differs from the candidate release")
        project_id, work_id, artifact_id = evidence
        work = self.request(f"/api/v1/projects/{project_id}/work-items/{work_id}")
        if work["work_item"]["title"] != "Preserve release smoke evidence":
            raise RuntimeError("Upgrade lost work data")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.ports['api']}/api/v1/projects/{project_id}/artifacts/{artifact_id}/content",
            headers={"Authorization": f"Bearer {self.key}",
                     "X-Artifact-Metadata": json.dumps({"agent_session_id": self.project,
                                                       "actor_client": "release-smoke"})},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.read() != b"Mnemonic release upgrade artifact\n":
                raise RuntimeError("Upgrade lost artifact bytes")

    def acceptance(self, root: Path) -> None:
        checker = root / "scripts/check-stack.py"
        with checker.open("rb") as source:
            self.command("exec", "-T", "mcp", "python", "-", "--api-url", "http://api:8000",
                         "--mcp-url", "http://mcp:8001/mcp", "--web-url", "http://web:3000",
                         "--web-origin", f"http://127.0.0.1:{self.ports['web']}",
                         stdin=source)
        # Audit a quiescent database, then restore serving for the final health check.
        self.command("stop", "web", "mcp", "api", "worker")
        for script in ("audit_project_activity.py", "audit_code_reviews.py"):
            self.command("run", "--rm", "--no-deps", "api", "python", f"/app/scripts/{script}")
        self.command("up", "-d", "--wait", "--wait-timeout", "180")


def exercise(args: argparse.Namespace, scratch: Path) -> None:
    candidate = unpack(args.bundle, scratch / "candidate")
    stack = Stack(scratch)
    try:
        settings = None
        if args.previous_bundle:
            previous = unpack(args.previous_bundle, scratch / "previous")
            settings = stack.configure(previous, args.previous_version, False, None)
            stack.command("pull")
            stack.command("up", "-d", "--wait", "--wait-timeout", "240")
            evidence = stack.seed()
            stack.verify(args.previous_version, evidence)
            stack.command("stop", "web", "mcp", "api", "worker")
        stack.configure(candidate, args.image_tag or args.version, args.local_images, settings)
        if not args.local_images:
            stack.command("pull")
        stack.command("up", "-d", "--wait", "--wait-timeout", "240")
        if not args.previous_bundle:
            evidence = stack.seed()
        stack.verify(args.version, evidence)
        stack.acceptance(candidate)
        print("PASS: isolated bundle install, persisted data, stack contract and integrity audits")
        if args.previous_bundle:
            print(f"PASS: upgrade from {args.previous_version} to {args.version}")
    except Exception:
        if stack.config.exists():
            stack.command("logs", "--no-color", "--tail", "50", "migrate", "api", "worker")
        raise
    finally:
        if stack.config.exists():
            stack.command("down", "--volumes", "--remove-orphans")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image-tag")
    parser.add_argument("--local-images", action="store_true")
    parser.add_argument("--previous-bundle", type=Path)
    parser.add_argument("--previous-version")
    args = parser.parse_args()
    if bool(args.previous_bundle) != bool(args.previous_version):
        parser.error("--previous-bundle and --previous-version must be supplied together")
    with tempfile.TemporaryDirectory(prefix="mnemonic-release-smoke-") as directory:
        exercise(args, Path(directory))


if __name__ == "__main__":
    main()
