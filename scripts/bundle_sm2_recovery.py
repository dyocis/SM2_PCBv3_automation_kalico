#!/usr/bin/env python3
"""Collect verified recovery files. Never restore, install, restart or flash."""
import argparse
import json
import os
from pathlib import Path

from preflight_sm2_i2c import audit, git_head
from stage_sm2_config import digest, inspect_tree, stage

MODULES = ("sm2_connection.py", "sm2_i2c.py", "sm2_sensor_health.py",
           "sm2_bme280.py", "sm2_sgp40.py")


def firmware_evidence(root, runtime):
    settings = dict(line.split("=", 1) for line in (root / ".config").read_text().splitlines()
                    if line.startswith("CONFIG_") and "=" in line)
    expected = {"CONFIG_MCU": '"stm32g0b1xx"',
                "CONFIG_CLOCK_REF_FREQ": "8000000",
                "CONFIG_FLASH_APPLICATION_ADDRESS": "0x8000000",
                "CONFIG_STM32_USB_PA11_PA12": "y"}
    if any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("Saved firmware build profile is not the reviewed SM2 G0B1 profile")
    dictionary = json.loads((root / "out/klipper.dict").read_text())
    if not runtime.get("mcu_version") or dictionary.get("version") != runtime["mcu_version"]:
        raise ValueError("Saved firmware dictionary version differs from running SM MCU")
    if dictionary.get("config", {}).get("MCU") != "stm32g0b1xx":
        raise ValueError("Saved firmware dictionary is for another MCU")
    if any(name.startswith("sm2_i2c_report_") for name in dictionary.get("commands", {})):
        raise ValueError("Recovery firmware already contains the experimental protocol")
    for name in ("klipper.bin", "klipper.elf"):
        if not (root / "out" / name).stat().st_size:
            raise ValueError("Empty saved firmware artifact: " + name)
    return dict(version=dictionary["version"], build_settings=expected,
                metadata_matches_runtime=True, flashed_binary_identity_verified=False)


def snapshot(path):
    resolved = path.resolve(strict=True)
    data = resolved.read_bytes()
    return data, dict(source=str(path.absolute()), resolved=str(resolved),
                     symlink=os.readlink(path) if path.is_symlink() else None,
                     mode=resolved.stat().st_mode & 0o777, sha256=digest(data))


def bundle(klipper, automation, config, firmware, info, status, output, head=git_head):
    report = audit(klipper, info, status, head=head)
    if not report["baseline_checks_passed"]:
        raise ValueError("Baseline audit failed: " + "; ".join(report["errors"]))
    if Path(info.get("config_file", "")).resolve() != config.resolve():
        raise ValueError("Config root differs from the running host config path")
    fw_evidence = firmware_evidence(firmware, status["mcu SM"])
    sources = [Path(entry["path"]) for entry in report["evidence"]["sources"]]
    sources += [firmware / ".config"] + [firmware / "out" / name for name in
               ("klipper.bin", "klipper.elf", "klipper.dict")]
    sources += [automation / "dashboard" / name for name in ("app.js", "nevermore_history.py")]
    inventory = []
    for name in MODULES:
        path = klipper / "klippy/extras" / name
        present = path.exists() or path.is_symlink()
        inventory.append(dict(path=str(path.absolute()), originally_present=present))
        if present:
            sources.append(path)  # A broken link must fail snapshot(), not become "absent".
    saved_vars = status["configfile"]["config"].get("save_variables", {}).get("filename")
    if not saved_vars:
        raise ValueError("Saved-variable file must be explicitly identified")
    variables = Path(saved_vars).expanduser()
    if not variables.is_absolute():
        raise ValueError("Relative saved-variable path requires manual review")
    sources.append(variables)
    # Snapshot before creating the directory so missing required files fail cleanly.
    snapshots = {path: snapshot(path) for path in dict.fromkeys(sources)}
    for source in report["evidence"]["sources"]:
        if snapshots[Path(source["path"])][1]["sha256"] != source["sha256"]:
            raise ValueError("Installed source changed after preflight")
    output = output.absolute()
    for path in (klipper, automation, config.parent, firmware, variables.parent):
        if output.resolve().is_relative_to(path.resolve()):
            raise ValueError("Recovery directory must be outside source directories")
    output.mkdir(parents=False, exist_ok=False)
    config_manifest = stage(config, status["configfile"]["config"], output / "config")
    artifacts = output / "artifacts"
    artifacts.mkdir()
    entries = []
    for index, (path, (data, metadata)) in enumerate(snapshots.items()):
        backup = "artifacts/%03d.bin" % index
        (output / backup).write_bytes(data)
        if digest((output / backup).read_bytes()) != metadata["sha256"]:
            raise ValueError("Recovery copy verification failed: " + str(path))
        entries.append(dict(metadata, backup=backup))
    for path, saved in snapshots.items():
        if snapshot(path) != saved:
            raise ValueError("Source changed during recovery capture: " + str(path))
    for entry in config_manifest["files"]:
        if digest(Path(entry["source"]).read_bytes()) != entry["sha256"]:
            raise ValueError("Config changed during recovery capture")
    current_configs, _ = inspect_tree(config)
    if {str(path): digest(data) for path, data in current_configs.items()} != {
            entry["source"]: entry["sha256"] for entry in config_manifest["files"]}:
        raise ValueError("Config include tree changed during recovery capture")
    if firmware_evidence(firmware, status["mcu SM"]) != fw_evidence:
        raise ValueError("Firmware metadata changed during recovery capture")
    current_report = audit(klipper, info, status, head=head)
    if not current_report["baseline_checks_passed"] or current_report["evidence"] != report["evidence"]:
        raise ValueError("Baseline source evidence changed during recovery capture")
    for entry in inventory:
        path = Path(entry["path"])
        if (path.exists() or path.is_symlink()) != entry["originally_present"]:
            raise ValueError("Module installation changed during recovery capture")
    evidence = dict(info=info, status=status, preflight=report)
    evidence_bytes = json.dumps(evidence, indent=2).encode()
    (output / "runtime-evidence.json").write_bytes(evidence_bytes)
    manifest = dict(schema=1, scope="SM2 migration recovery files", installation_allowed=False,
                    artifacts=entries, module_inventory=inventory, firmware=fw_evidence,
                    config_manifest="config/manifest.json",
                    config_manifest_sha256=digest((output / "config/manifest.json").read_bytes()),
                    runtime_evidence_sha256=digest(evidence_bytes),
                    limitations=["No MCU flash readback; version/profile match does not prove binary identity",
                                 "Not a full OS, Python environment, or repository backup",
                                 "Runtime evidence is a snapshot; recheck before installation",
                                 "Manual restore must preserve recorded symlink targets and permissions"])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("klipper", "automation", "config", "firmware", "evidence", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text())
    bundle(args.klipper, args.automation, args.config, args.firmware,
           evidence["info"], evidence["status"], args.output)
    print("Recovery copies verified. No live configuration, service, or firmware changed.")
    print("Saved firmware metadata matches the supplied runtime snapshot; no flash readback performed.")


if __name__ == "__main__":
    main()
