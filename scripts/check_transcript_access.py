"""Check transcript access using the real API/worker identities and Compose mounts.

Run after deployment, or with --one-off before starting a newly built installation.
The bounded sample opens/closes files without reading their bodies. Configuration
secrets and arbitrary Docker inspection output never enter this report.
"""

import argparse
import json
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one-off", action="store_true", help="Use built service images")
    parser.add_argument("--sample", type=int, default=100)
    args = parser.parse_args()
    reports = {}
    failed = False
    for service in ("api", "worker"):
        command = ["docker", "compose"]
        command += (["run", "--rm", "--no-deps", "-T"] if args.one_off else ["exec", "-T"])
        command += [service, "python", "-m", "mnemonic_api.transcript_doctor",
                    "--sample", str(args.sample)]
        if service == "worker":
            command.append("--worker")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = {"error": "Service diagnostics unavailable. Check Compose service startup "
                      "and source bind mounts; rebuild API and worker with the current release."}
        reports[service] = report
        failed |= result.returncode != 0
    api, worker = reports["api"], reports["worker"]
    mismatch = any(api.get(key) != worker.get(key) for key in ("uid", "gid", "roots"))
    print(json.dumps({"services": reports, "configuration_mismatch": mismatch}, indent=2))
    raise SystemExit(1 if failed or mismatch else 0)


if __name__ == "__main__":
    main()
