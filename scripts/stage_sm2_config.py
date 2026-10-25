#!/usr/bin/env python3
"""Stage four ambient factory edits and config backups; never edit live files."""
import argparse
import configparser
import difflib
import glob
import hashlib
import json
from pathlib import Path
import re

TARGETS = {"temperature_sensor " + name: kind for name, kind in (
    ("BME_IN", "BME280"), ("BME_OUT", "BME280"),
    ("SGP_IN", "SGP40"), ("SGP_OUT", "SGP40"))}
HEADER = re.compile(r"^\[([^\]]+)\]\s*$")
FACTORY = re.compile(r"^(\s*sensor_type\s*[:=]\s*)(BME280|SGP40)(\s*(?:#.*)?)(\r?\n)?$")
REGISTRATION = "# Register opt-in ambient sensor factories before sensor sections.\n[sm2_bme280]\n\n[sm2_sgp40]\n\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inspect_tree(main, staging=None):
    files, targets, visited, contains_targets = {}, {}, set(), {}

    def visit(path, stack):
        path = path.resolve(strict=True)
        if path in stack:
            raise ValueError("Recursive include: " + str(path))
        if path in visited:
            if contains_targets.get(path, True):
                raise ValueError("Repeated include with target sensors requires review: " + str(path))
            return
        previous_targets = set(targets)
        visited.add(path)
        data = path.read_bytes()
        lines = data.decode("utf-8").splitlines(keepends=True)
        files[path] = data
        current, start = None, 0

        def finish(end):
            if current is None:
                return
            if current in ("sm2_bme280", "sm2_sgp40"):
                raise ValueError("Prototype registration already present: " + current)
            if current.startswith("include "):
                spec = current[8:].strip()
                include_path = path.parent / spec
                if staging is not None and glob.has_magic(str(include_path)):
                    prefix = include_path.anchor
                    for part in include_path.parts[1:]:
                        if glob.has_magic(part):
                            break
                        prefix = str(Path(prefix) / part)
                    if staging.resolve().is_relative_to(Path(prefix).resolve()):
                        raise ValueError("Staging directory overlaps an include glob search root")
                if any(line.split("#", 1)[0].strip() for line in lines[start + 1:end]):
                    raise ValueError("Parameterized include requires review: " + str(path))
                matches = sorted(glob.glob(str(path.parent / spec), recursive=True))
                if not matches:
                    raise ValueError("Include matched no files: " + spec)
                for match in matches:
                    visit(Path(match), stack + [path])
            elif current in TARGETS:
                if current in targets:
                    raise ValueError("Duplicate target section: " + current)
                parser = configparser.RawConfigParser(strict=True, inline_comment_prefixes=("#",))
                parser.read_string("".join(lines[start:end]))
                settings = dict(parser.items(current))
                if settings.get("sensor_type") != TARGETS[current]:
                    raise ValueError("Unexpected baseline factory: " + current)
                edits = [i for i in range(start + 1, end) if FACTORY.match(lines[i])]
                if len(edits) != 1:
                    raise ValueError("Ambiguous sensor_type edit: " + current)
                targets[current] = dict(path=path, line=edits[0], settings=settings)

        for index, line in enumerate(lines):
            active = line.split("#", 1)[0].strip()
            if "!!include" in active:
                raise ValueError("Inline include requires review: " + str(path))
            match = HEADER.match(active)
            if match:
                finish(index)
                current, start = match[1], index
        finish(len(lines))
        contains_targets[path] = set(targets) != previous_targets

    visit(main, [])
    if set(targets) != set(TARGETS):
        raise ValueError("Missing target sections: " + ", ".join(sorted(set(TARGETS) - set(targets))))
    return files, targets


def plan(main, runtime_config, staging=None):
    files, targets = inspect_tree(main, staging)
    staged = {}
    for name, target in targets.items():
        # Calibration saved in SAVE_CONFIG is not an edit target. Compare all
        # transport/reference options, including unexpected extra options.
        def relevant(settings):
            return {k: str(v).strip() for k, v in settings.items()
                    if k == "sensor_type" or k.startswith("i2c_")
                    or k.startswith("ref_") or k == "sync_with"}
        if relevant(target["settings"]) != relevant(runtime_config.get(name, {})):
            raise ValueError("Disk/runtime sensor configuration differs: " + name)
        path = target["path"]
        lines = staged.setdefault(path, files[path].decode("utf-8").splitlines(keepends=True))
        index = target["line"]
        match = FACTORY.match(lines[index])
        lines[index] = match[1] + "SM2_" + match[2] + match[3] + (match[4] or "")
    candidates = {path: "".join(lines).encode("utf-8") for path, lines in staged.items()}
    root = main.resolve(strict=True)
    original = candidates.get(root, files[root])
    newline = "\r\n" if b"\r\n" in original else "\n"
    candidates[root] = REGISTRATION.replace("\n", newline).encode() + original
    return files, candidates


def stage(main, runtime_config, output):
    output = output.absolute()
    files, candidates = plan(main, runtime_config, output)
    # Never stage inside the active configuration tree, where globs could load it.
    for path in files:
        if output.resolve().is_relative_to(path.parent):
            raise ValueError("Staging directory must be outside active config directories")
    for path, data in files.items():
        if path.read_bytes() != data:
            raise ValueError("Configuration changed during inspection: " + str(path))
    output.mkdir(parents=False, exist_ok=False)
    entries, diffs = [], []
    for index, (path, data) in enumerate(sorted(files.items())):
        backup = "%03d-original.cfg" % index
        (output / backup).write_bytes(data)
        entry = dict(source=str(path), backup=backup, sha256=digest(data),
                     mode=path.stat().st_mode & 0o777)
        if path in candidates:
            candidate = "%03d-candidate.cfg" % index
            changed = candidates[path]
            (output / candidate).write_bytes(changed)
            entry.update(candidate=candidate, candidate_sha256=digest(changed))
            diffs.extend(difflib.unified_diff(data.decode().splitlines(True),
                         changed.decode().splitlines(True), str(path), str(path) + " (candidate)"))
        entries.append(entry)
    for entry in entries:
        if digest((output / entry["backup"]).read_bytes()) != entry["sha256"]:
            raise ValueError("Backup verification failed")
        if "candidate" in entry and digest((output / entry["candidate"]).read_bytes()) != entry["candidate_sha256"]:
            raise ValueError("Candidate verification failed")
    current_files, current_candidates = plan(main, runtime_config, output)
    if current_files != files or current_candidates != candidates:
        raise ValueError("Configuration changed during backup; stage is incomplete")
    if any(Path(entry["source"]).stat().st_mode & 0o777 != entry["mode"] for entry in entries):
        raise ValueError("Configuration permissions changed during backup")
    # Written last: a partial directory without this manifest is not a completed stage.
    (output / "changes.diff").write_text("".join(diffs), encoding="utf-8")
    manifest = dict(schema=1, installation_allowed=False, scope="configuration-only",
                    sensor_registration=["sm2_bme280", "sm2_sgp40"],
                    files=entries, runtime_config_sha256=digest(json.dumps(runtime_config, sort_keys=True).encode()),
                    remaining=["Complete host/module/firmware recovery bundle",
                               "Fresh live preflight and reviewed deployment",
                               "Output reconciliation and attended hardware validation"])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True,
                        help="JSON of configfile.config (not the full Moonraker response)")
    parser.add_argument("--output", type=Path, required=True, help="New directory outside active config")
    args = parser.parse_args()
    stage(args.config, json.loads(args.runtime_config.read_text()), args.output)
    print("Config backups verified and candidate diff staged. No live files changed.")
    print("This is not a complete firmware/host recovery bundle or an install approval.")


if __name__ == "__main__":
    main()
