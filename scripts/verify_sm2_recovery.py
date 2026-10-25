#!/usr/bin/env python3
"""Verify saved recovery checksums without contacting or modifying a printer."""
import json
from pathlib import Path
import sys
from stage_sm2_config import digest


def verify(root):
    root = root.resolve(strict=True)

    def checked(name, sha=None):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid bundle path: " + name)
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Bundle path escapes recovery directory: " + name)
        data = path.read_bytes()
        if sha is not None and digest(data) != sha:
            raise ValueError("Recovery checksum mismatch: " + name)
        return data

    manifest = json.loads(checked("manifest.json"))
    if manifest.get("schema") != 1 or manifest.get("scope") != "SM2 migration recovery files":
        raise ValueError("Unknown recovery manifest")
    for entry in manifest["artifacts"]:
        checked(entry["backup"], entry["sha256"])
    checked("runtime-evidence.json", manifest["runtime_evidence_sha256"])
    config_name = manifest["config_manifest"]
    config = json.loads(checked(config_name, manifest["config_manifest_sha256"]))
    parent = Path(config_name).parent
    for entry in config["files"]:
        checked(str(parent / entry["backup"]), entry["sha256"])
        if "candidate" in entry:
            checked(str(parent / entry["candidate"]), entry["candidate_sha256"])
    return manifest


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
    print("Recovery artifact and configuration checksums verified.")
    print("This verifies saved copies, not flashed firmware identity or install readiness.")
