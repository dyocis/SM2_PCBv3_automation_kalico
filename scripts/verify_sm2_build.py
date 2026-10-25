#!/usr/bin/env python3
"""Validate the CI build target and compiled protocol; never flash hardware."""
import json
from pathlib import Path
import sys


def verify(root):
    config = dict(line.split("=", 1) for line in
                  (root / ".config").read_text().splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
    expected = {
        "CONFIG_MCU": '"stm32g0b1xx"',
        "CONFIG_FLASH_APPLICATION_ADDRESS": "0x8000000",
        "CONFIG_CLOCK_REF_FREQ": "8000000",
        "CONFIG_STM32_USB_PA11_PA12": "y",
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError("Unexpected %s: %r" % (key, config.get(key)))
    data = json.loads((root / "out/klipper.dict").read_text())
    command = "sm2_i2c_report_v1 oid=%c data=%*s read_len=%c"
    response = "sm2_i2c_result_v1 oid=%c status=%c response=%*s"
    if command not in data["commands"] or response not in data["responses"]:
        raise ValueError("Compiled firmware is missing the SM2 reporting protocol")
    if not (root / "out/klipper.bin").stat().st_size:
        raise ValueError("Empty firmware binary")
    print("Verified G0B1 / 8 MHz / USB / no bootloader and compiled SM2 protocol")
    print("Build validation only; no firmware installed or hardware tested.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
