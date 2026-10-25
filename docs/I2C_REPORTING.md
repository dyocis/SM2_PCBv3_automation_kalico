# Experimental SM2 I2C reporting

This is an offline development candidate, not installed on the printer and not
enabled by the installer. Installation integration and hardware validation are still
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

## Sensor integration candidate

### Fault notifications and statistics

Each prototype sensor emits a direct console message on fault entry, a message
when its retry budget is exhausted, and recovery only after a complete valid
sample. Repeated attempts within one unresolved incident do not repeat console
alerts. Rapid new incidents are limited to one announced incident per sensor
per minute; all incident/recovery transitions still enter `klippy.log`.

The driver's `health` status contains `incident_count`, `failed_attempt_count`,
`errors_by_kind`, `incidents_by_kind`, `fault_active`, `retries_exhausted`,
`last_error`, `last_fault_utc`, `last_recovery_utc`, and `observed_seconds`.
Categories distinguish confirmed firmware `i2c_bus` statuses from `transport`
query failures, `crc`, compensation `reference`, `protocol`, and other `sensor`
errors. Multiple retries are not independent incidents. A single incident can
involve multiple categories; category incident counts must not be summed as
independent physical failures. Two sensors can also share one physical fault.

Counters reset when the host process restarts; they are not lifetime counters.
Existing log retention/rotation controls how long event history survives.
Notifications are local console/log messages, not phone or email alerts.
No notification behavior is installed on the running printer yet.

The dashboard and recorder subscribe to the full BME280/SGP40 driver status.
When a driver supplies `valid`, it takes precedence over legacy temperature
wrappers and cached macro numbers. Invalid readings appear unavailable and
cannot produce efficiency estimates or accepted environmental history samples.
A recovered driver supplies the new values directly. Drivers without a
validity field retain their existing display behavior; that compatibility
path does not certify their readings as fresh. Automation guards are described
below; physical output reconciliation remains unfinished work.

An uninstalled `sm2_bme280.py` prototype registers the separate `SM2_BME280`
factory for ambient `temperature_sensor` sections only. It subclasses the
pinned Kalico BME280 implementation for calibration and compensation, routes
initialization and sampling through the reporting adapter, checks chip ID,
bounds initialization waits and consecutive retries, and exposes `valid`,
sample age, error and restart-required state. Invalid/stale numeric fields are
`None`, not zero. MCU reconnect is latched until host restart. Configured
temperature limits still invoke shutdown. Tests load the actual pinned base
driver fixture, including its initializer and Bosch reference compensation.

This driver is not yet suitable for enabling: existing SGP40 compensation,
macros and dashboard consumers must handle invalid/`None` readings first.
The fixture `tests/fixtures/kalico_bme280.py` is the unmodified
`klippy/extras/bme280.py` from the pinned commit above; its upstream copyright
and GPL notice are retained. Runtime compatibility with other base-driver
revisions has not been established.

The uninstalled `sm2_sgp40.py` prototype registers `SM2_SGP40`, subclassing
thetic/klipper-sgp40 revision `d07664022fd24a6b0f26305d88dfbbd84213d993`.
Its unmodified `src/klipper_sgp40/__init__.py` is retained with upstream notices
as `tests/fixtures/sgp40_upstream.py`. Runtime installation requires that
package and its gas-index algorithm; no global bus monkey patch is used.

The prototype validates every returned word's CRC before VOC processing,
rejects a failed self-test, requires validity-aware temperature/humidity
references, and retains the compensation associated with the measurement
that produced each sample. Failed measurements do not train the VOC algorithm
or emit fake zero callbacks. Retries are bounded, including repeated CRC
failures interspersed with successful measurement-start writes. A reconnect
requires restart. Queries report unavailable state and calibration saving is
refused while readings are unavailable. Both synchronized peers must use the
new driver. Tests use a recording algorithm to prove invalid samples are not
processed; they do not validate VOC chemistry, accuracy or long-term learning.

`test_sm2_real_gia.py` additionally exercises the actual pinned `gia.py`
algorithm with the BME280 and SGP40 prototypes. Algorithm blackout values are
reported as unavailable/WARMING_UP, never as clean-air zero callbacks. Invalid
CRC and compensation readings do not advance learning. Short interruptions
reset the algorithm and restore its saved states per the upstream interface;
interruptions exceeding ten minutes require restart instead of automatically
reusing old learning. The upstream file is retained as `sgp40_gia.py` in the
test fixtures. These are software integration tests, not accuracy validation.

- Integrate the prototype drivers with installer preflight/version checks.
  Ordinary sensor instances must retain
  their original transport. Do not enable either prototype with numeric-only
  consumers. Missing readings must not be interpreted as zero.
- Validate calibration readiness on hardware following the software tests of
  both drivers with the actual gas-index algorithm and extended sample gaps.
- Reconcile outputs and automation with driver failure/restart states.
  Reconnection or restored measurements must not re-arm cooling or UV.
- Validate the STM32 bus after NACK and timeout. Reporting a failure does not
  establish that a stuck bus has recovered.
- Require the complete G0B1 firmware build and validate attended hardware behavior
  while the printer is idle before enabling the candidate.

The command harness checks protocol logic with simulated buses, not STM32
electrical behavior, firmware timing, physical output safety, or sensor accuracy.

## Automation guard candidate

The connection monitor now separately reports `sensors_ready`,
`sensors_managed`, and `sensor_restart_required`. Once any validity-aware
driver is observed, all four configured BME/SGP objects must provide valid
readings. Initial warm-up inhibits starts without latching a permanent fault.
After all four have been valid, a later invalid reading latches the start
inhibit until host restart. Sensor recovery alone never clears that latch.
Legacy drivers without validity keep the prior behavior; that compatibility
path is not evidence of sensor freshness.

Positive Peltier/UV requests and delayed Peltier enable recheck sensor
readiness. Sensor-dependent automation, status/cache updates and calibration
are guarded before reading numeric values. The safety monitor and rejected
start paths invoke `NEVERMORE_SENSOR_INHIBIT`, which cancels pending Peltier
enable and routes active optional outputs through their existing OFF macros.
Those OFF paths retain fan cooldown. Calibration timers abort instead of
saving baselines or leaving a preparation phase stuck. Thermal trips still
run without ambient readings, using -1 for unavailable chamber evidence.

The inhibit and normal OFF paths also inspect the host's `output_pin.value`
so a stale OFF macro flag cannot conceal a reported energized UV/Peltier pin.
They establish cooldown before invoking the fan macro. Fan reduction is blocked
while a pin is reported on or cooldown is active, and calibration cannot reject
airflow required by those conditions. Ordinary calibration start restrictions
remain. Host pin status describes commanded state, not electrical feedback;
this cannot detect a stuck MOSFET, disconnected fan or lost MCU communication.

This does not make MCU disconnection physically safe: connection loss still
prevents hardware commands. It also cannot retract already-rendered/queued
commands. Attended validation of timing, actual output state and airflow is
required before deployment. Installer version checks are still outstanding.

## Complete firmware build check

The Validate workflow builds pinned Kalico with the reporting extension using
an ARM compiler. `firmware/sm2-g0b1.config` selects STM32G0B1, an 8 MHz crystal,
USB PA11/PA12 and no bootloader offset, matching the configuration reported for
the development printer. `scripts/verify_sm2_build.py` checks the resolved
configuration, binary, and compiled protocol dictionary. A different board
must have its own verified configuration before flashing.

This CI job never connects to or flashes a printer and does not publish a
firmware release. Passing compilation does not demonstrate bus recovery,
electrical behavior, sensor accuracy, or output safety on hardware.
