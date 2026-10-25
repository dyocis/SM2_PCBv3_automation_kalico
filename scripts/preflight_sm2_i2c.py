#!/usr/bin/env python3
"""Read-only baseline audit. Does not install, flash, restart, or enable outputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen

from prepare_sm2_i2c import BASE_COMMIT, ROOT, normalized

SGP_COMMIT = "d07664022fd24a6b0f26305d88dfbbd84213d993"
SENSORS = ("BME_IN", "BME_OUT", "SGP_IN", "SGP_OUT")


def git_head(path):
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True,
        stderr=subprocess.PIPE, timeout=10).strip()


def audit(klipper, info, status, head=git_head):
    """Audit a pre-migration baseline, never grant installation permission."""
    errors, evidence = [], {}

    def require(condition, message):
        if not condition:
            errors.append(message)

    klipper = klipper.resolve()
    require(info.get("app") == "Kalico", "Running host is not identified as Kalico")
    require(info.get("state") == "ready", "Printer is not ready")
    require(info.get("software_version") == "v2026.10.00-157-g3ec85e6a",
            "Running host version differs from the supported clean baseline")
    require(Path(info.get("klipper_path", "")).resolve() == klipper,
            "Audited checkout differs from running host path")
    require(status.get("webhooks", {}).get("state") == "ready", "Host status is not ready")
    require(status.get("print_stats", {}).get("state") in ("standby", "complete", "cancelled"),
            "Print state is missing, active, paused, or failed")
    require(status.get("idle_timeout", {}).get("state") == "Idle", "Printer is not idle")
    for pin in ("peltier", "uv"):
        require(status.get("output_pin " + pin, {}).get("value") == 0,
                "Output %s is not confirmed off" % pin)
    config_status = status.get("configfile", {})
    require(config_status.get("save_config_pending") is False,
            "Pending config save state is missing or not clear")
    config = config_status.get("config", {})
    sm = config.get("mcu SM", {})
    require(str(sm.get("is_non_critical", "")).lower() in ("true", "1"),
            "SM MCU must be explicitly non-critical")
    serial = sm.get("serial", "")
    require(Path(serial).parent.as_posix() == "/dev/serial/by-id"
            and Path(serial).name.startswith("usb-Klipper_stm32g0b1xx_"),
            "SM serial identity does not match the G0B1 USB development target")
    evidence["sm_serial"] = serial
    evidence["sm_runtime"] = status.get("mcu SM", {})
    require(bool(evidence["sm_runtime"].get("mcu_version")), "SM firmware version is missing")
    for name in SENSORS:
        section = config.get("temperature_sensor " + name, {})
        kind = "BME280" if name.startswith("BME") else "SGP40"
        require(section.get("sensor_type") == kind,
                name + " must use the original baseline factory " + kind)
        require(section.get("i2c_mcu") == "SM", name + " must use the SM MCU")
        bus = "i2c1_PB8_PB9" if name.endswith("IN") else "i2c2_PB10_PB11"
        require(section.get("i2c_bus") == bus, name + " has an unexpected hardware bus")
        require(not any(key.startswith("i2c_software_") for key in section),
                name + " has unreviewed software I2C settings")
        try:
            address = int(str(section.get("i2c_address", "119" if kind == "BME280" else "89")), 0)
        except ValueError:
            address = -1
        require(address == (119 if kind == "BME280" else 89), name + " has an unexpected address")
        if kind == "SGP40":
            reference = "bme280 BME_" + name.split("_")[1]
            for field in ("ref_temp_sensor", "ref_humidity_sensor"):
                require(section.get(field) == reference, name + " has an unexpected " + field)
            peer = "SGP_OUT" if name.endswith("IN") else "SGP_IN"
            require(section.get("sync_with") == peer, name + " has an unexpected sync peer")
    for name, section in config.items():
        if section.get("i2c_mcu") == "SM" and name not in ["temperature_sensor " + n for n in SENSORS]:
            errors.append("Unreviewed additional SM I2C device: " + name)
        if str(section.get("sensor_type", "")).startswith("SM2_"):
            errors.append("Prototype factory already configured: " + name)
    try:
        evidence["kalico_commit"] = head(klipper)
        require(evidence["kalico_commit"] == BASE_COMMIT, "Unsupported Kalico checkout revision")
        extras = klipper / "klippy/extras"
        sources = ((extras / "bme280.py", "kalico_bme280.py"),
                   (extras / "sgp40/__init__.py", "sgp40_upstream.py"),
                   (extras / "sgp40/gia.py", "sgp40_gia.py"),
                   (klipper / "src/i2ccmds.c", "kalico_i2ccmds.c"))
        evidence["sources"] = []
        for path, fixture in sources:
            resolved = path.resolve(strict=True)
            data = resolved.read_bytes()
            require(normalized(data) == normalized((ROOT / "tests/fixtures" / fixture).read_bytes()),
                    "Installed source differs from tested baseline: " + str(path))
            evidence["sources"].append(dict(path=str(path), resolved=str(resolved),
                                             sha256=hashlib.sha256(data).hexdigest()))
        evidence["sgp40_commit"] = head((extras / "sgp40").resolve(strict=True))
        require(evidence["sgp40_commit"] == SGP_COMMIT, "Unsupported installed SGP40 revision")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append("Source inspection failed: " + str(exc))
    return dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(),
                baseline_checks_passed=not errors, installation_allowed=False,
                errors=errors, evidence=evidence,
                remaining=["Resolve on-disk include tree and reject ambiguous edits",
                           "Verify board build profile and running firmware protocol",
                           "Create and verify matched recovery bundle and staged manifest",
                           "Recheck idle/output state immediately before any write",
                           "Attended output reconciliation and hardware acceptance"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--klipper-path", type=Path, default=Path.home() / "klipper")
    parser.add_argument("--port", type=int, default=7125)
    parser.add_argument("--evidence-output", type=Path,
                        help="Write a new private runtime snapshot after a passing audit")
    args = parser.parse_args()

    def get(path):
        with urlopen("http://127.0.0.1:%d%s" % (args.port, path), timeout=10) as response:
            return json.load(response)["result"]

    try:
        info = get("/printer/info")
        names = ("webhooks", "configfile", "print_stats", "idle_timeout",
                 "mcu%20SM", "output_pin%20peltier", "output_pin%20uv")
        status = get("/printer/objects/query?" + "&".join(names))["status"]
        report = audit(args.klipper_path, info, status)
        if report["baseline_checks_passed"] and args.evidence_output:
            with args.evidence_output.open("x", encoding="utf-8") as evidence:
                json.dump(dict(info=info, status=status), evidence, indent=2)
    except (OSError, ValueError, KeyError) as exc:
        report = dict(baseline_checks_passed=False, installation_allowed=False,
                      errors=["Unable to obtain live audit evidence: " + str(exc)])
    print(json.dumps(report, indent=2))
    return 0 if report["baseline_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
