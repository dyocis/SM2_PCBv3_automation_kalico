#!/usr/bin/env python3
"""Prepare a reviewable host-file deployment/rollback plan. Never apply it."""
import argparse
import difflib
import json
from pathlib import Path

from bundle_sm2_recovery import MODULES
from stage_sm2_config import digest
from verify_sm2_recovery import verify


def prepare(recovery, candidate, output):
    recovery = recovery.resolve(strict=True)
    candidate = candidate.resolve(strict=True)
    manifest = verify(recovery)
    evidence = json.loads((recovery / "runtime-evidence.json").read_text())
    klipper = Path(evidence["info"]["klipper_path"])
    config_manifest = json.loads((recovery / manifest["config_manifest"]).read_text())
    if config_manifest.get("sensor_registration") != ["sm2_bme280", "sm2_sgp40"]:
        raise ValueError("Rebuild recovery/config stage with sensor factory registration")
    entries = {}
    for entry in config_manifest["files"]:
        entries[entry["source"]] = dict(entry, backup="config/" + entry["backup"])
    for entry in manifest["artifacts"]:
        if entry["source"] in entries and entries[entry["source"]]["sha256"] != entry["sha256"]:
            raise ValueError("Conflicting recovery copies: " + entry["source"])
        entries[entry["source"]] = entry
    operations = {}

    def add(destination, data, reason):
        destination = str(destination)
        if destination in operations:
            raise ValueError("Overlapping deployment edits: " + destination)
        original = entries.get(destination)
        if original is None:
            matches = [i for i in manifest["module_inventory"] if i["path"] == destination]
            if len(matches) != 1 or matches[0]["originally_present"]:
                raise ValueError("Destination has no recovery evidence: " + destination)
        operations[destination] = dict(data=data, original=original, reason=reason)

    for entry in config_manifest["files"]:
        if "candidate" in entry:
            add(entry["source"], (recovery / "config" / entry["candidate"]).read_bytes(),
                "Sensor factory selection and registration")
    for source in sorted((candidate / "config").glob("*.cfg")):
        matches = [e for e in config_manifest["files"] if Path(e["source"]).name == source.name]
        if len(matches) != 1:
            raise ValueError("Expected one active config for " + source.name)
        add(matches[0]["source"], source.read_bytes(), "Reviewed automation candidate")
    for name in MODULES:
        add(klipper / "klippy/extras" / name,
            (candidate / "klippy/extras" / name).read_bytes(), "Matching host module")
    for name in ("app.js", "nevermore_history.py"):
        matches = [e for e in manifest["artifacts"] if Path(e["source"]).name == name
                   and Path(e["source"]).parent.name == "dashboard"]
        if len(matches) != 1:
            raise ValueError("Expected one backed-up dashboard asset: " + name)
        add(matches[0]["source"], (candidate / "dashboard" / name).read_bytes(), "Validity-aware dashboard/history")
    # Freeze both forward and rollback bytes into a separate review directory.
    output = output.absolute()
    for path in (recovery, candidate, klipper):
        if output.resolve().is_relative_to(path.resolve()):
            raise ValueError("Plan directory must be outside source/recovery trees")
    for destination in operations:
        if output.resolve().is_relative_to(Path(destination).resolve().parent):
            raise ValueError("Plan directory must be outside destination directories")
    output.mkdir(parents=False, exist_ok=False)
    changes, diff = [], []
    resolved_destinations = set()
    for index, (destination, operation) in enumerate(sorted(operations.items())):
        original = operation["original"]
        before = (recovery / original["backup"]).read_bytes() if original else b""
        after = operation["data"]
        resolved = original.get("resolved", destination) if original else destination
        if resolved in resolved_destinations:
            raise ValueError("Two deployment targets resolve to the same file: " + resolved)
        resolved_destinations.add(resolved)
        forward = "%03d-forward.bin" % index
        (output / forward).write_bytes(after)
        rollback = "%03d-rollback.bin" % index if original else None
        if rollback:
            (output / rollback).write_bytes(before)
        diff.extend(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                                        destination + " (current)", destination + " (candidate)"))
        changes.append(dict(destination=destination, resolved_destination=resolved,
                            forward=forward, after_sha256=digest(after), rollback=rollback,
                            before_sha256=digest(before) if original else None,
                            originally_present=original is not None,
                            original_symlink=original.get("symlink") if original else None,
                            original_mode=original.get("mode") if original else None,
                            reason=operation["reason"]))
    for change in changes:
        for field, checksum in (("forward", "after_sha256"), ("rollback", "before_sha256")):
            if change[field] and digest((output / change[field]).read_bytes()) != change[checksum]:
                raise ValueError("Deployment payload verification failed")
    (output / "changes.diff").write_text("".join(diff), encoding="utf-8")
    result = dict(schema=1, scope="host-file review plan", installation_allowed=False,
                  changes=changes, recovery_manifest_sha256=digest((recovery / "manifest.json").read_bytes()),
                  requirements=["Review every macro diff for local customization",
                                "Fresh idle/output checks and stopped host before applying",
                                "Matching SM2 reporting firmware must be built and flashed separately",
                                "Restore original files/links; remove only originally absent modules",
                                "Attended startup, output and fault acceptance"])
    (output / "plan.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recovery", "candidate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.recovery, args.candidate, args.output)
    print("Host-file deployment and rollback payloads staged for review. Nothing applied.")
