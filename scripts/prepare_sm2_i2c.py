#!/usr/bin/env python3
"""Prepare (never flash) an opt-in I2C reporting command for pinned Kalico."""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "3ec85e6aae670182955cf9f4cdc968cf1fba1315"


def normalized(data):
    return data.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"


def patched_source(original):
    baseline = normalized((ROOT / "tests/fixtures/kalico_i2ccmds.c").read_bytes())
    extra = normalized((ROOT / "firmware/sm2_i2c_report.inc").read_bytes())
    original = normalized(original)
    patched = baseline + extra
    if original == patched:
        return patched
    if original != baseline:
        raise ValueError("i2ccmds.c differs from pinned Kalico %s; review required"
                         % BASE_COMMIT)
    return patched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Kalico src/i2ccmds.c to inspect")
    parser.add_argument("--output", type=Path, required=True,
                        help="new output file; source is never modified")
    args = parser.parse_args()
    data = patched_source(args.source.read_bytes())
    with args.output.open("xb") as out:
        out.write(data)
    print("Prepared:", args.output)
    print("SHA256:", hashlib.sha256(data).hexdigest())
    print("No source checkout changed. Nothing built, installed, or flashed.")


if __name__ == "__main__":
    main()
