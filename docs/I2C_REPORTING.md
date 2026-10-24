# Experimental SM2 I2C reporting

This is an offline development candidate, not installed on the printer and not
enabled by the installer. Sensor integration and hardware validation are still
required. The supported source is Kalico commit
`3ec85e6aae670182955cf9f4cdc968cf1fba1315`.

## Protocol

`firmware/sm2_i2c_report.inc` adds a separate command to `src/i2ccmds.c`:

- Request: `sm2_i2c_report_v1 oid=%c data=%*s read_len=%c`
- Response: `sm2_i2c_result_v1 oid=%c status=%c response=%*s`

Status 0 means success; 1 NACK, 2 timeout, 3 start NACK, 4 read-start NACK,
5 invalid transfer length, and 6 unconfigured bus. Transfers are limited to
32 bytes in each direction. A failed transaction returns no measurement bytes.
An invalid object identifier retains the existing firmware error behavior.
Existing I2C commands and their shutdown behavior are unchanged.

The host adapter permits only non-critical MCUs and SGP40/BME280 addresses.
It requires the new firmware command and never falls back to fatal legacy
commands. Writes wait for their status response. Queries disable automatic
replay because repeating a write can restart or reset a sensor. Disconnection,
reconnection, incorrect response identity, short data, and error statuses are
rejected rather than returned as valid samples.

The firmware itself does not enforce device addresses or non-critical status;
those restrictions are host-side checks. This command must not replace I2C
transactions for temperature protection or other critical devices.

## Offline preparation and tests

Run `python scripts/test_sm2_i2c.py`. With `CC=gcc`, the suite requires a C
compiler and compiles the actual pinned command source plus the extension
against injected hardware/software bus implementations. It checks success,
each bus error, transfer bounds, invalid configuration, suppression of error
payloads, and preservation of legacy shutdowns. Without a compiler, that test
is explicitly skipped; CI requires it.

`python scripts/prepare_sm2_i2c.py PATH/TO/src/i2ccmds.c --output candidate.c`
creates a new file only. It accepts the exact pinned source (normalizing line
endings), or the same candidate for idempotence, and refuses unknown source or
an existing output. It does not install, build or flash firmware.

## Remaining integration work

- Route all opted-in SGP40/BME280 operations, including initialization and
  buffered writes, through this adapter. Ordinary sensor instances must keep
  their original transport.
- Catch transport errors in the drivers, invalidate samples immediately, and
  expose freshness/error state to automation and history. Zero is not a valid
  replacement for missing temperature/humidity data.
- Reject SGP40 CRC failures and prevent invalid samples from updating its VOC
  algorithm or saved calibration.
- Bound retries, reinitialize sensors deliberately, and respect the connection
  monitor's restart latch. Reconnection must not re-arm cooling or UV.
- Validate the STM32 bus after NACK and timeout. Reporting a failure does not
  establish that a stuck bus has recovered.
- Build the complete G0B1 firmware and validate attended hardware behavior
  while the printer is idle before enabling the candidate.

The command harness checks protocol logic with simulated buses, not STM32
electrical behavior, firmware timing, physical output safety, or sensor accuracy.
