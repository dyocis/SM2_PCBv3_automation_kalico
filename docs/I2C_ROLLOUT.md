# I2C candidate rollout requirements

The candidate remains uninstalled. This document defines the installation
contract; it is not authorization to flash a board or a completed installer.

## Preflight before any write

- Require an idle printer and record the running host version, SM MCU version,
  configured serial identity, and enabled sensor sections from Moonraker.
- Verify the Kalico checkout is the supported commit
  `3ec85e6aae670182955cf9f4cdc968cf1fba1315`. Verify the loaded BME280 source
  against the pinned fixture and the loaded SGP40 package against the supported
  `thetic/klipper-sgp40` revision
  `d07664022fd24a6b0f26305d88dfbbd84213d993`, including its gas-index module.
  Checking a separate unused checkout is insufficient; resolve installed links.
- Resolve the active config include tree. Require exactly the intended
  BME_IN/BME_OUT/SGP_IN/SGP_OUT instances on the non-critical SM MCU. Verify
  both SGP compensation references and synchronized peers. Refuse ambiguous
  duplicate sections or unexpected sensor names rather than editing guesses.
- Verify the SM2 board identity and its existing build configuration. The CI
  G0B1/8 MHz/USB/no-offset profile is specific to the development printer.
- Save the current config, installed module/link targets, firmware binary and
  build configuration, commit IDs, and checksums in a dated recovery bundle.
  Preserve saved VOC baselines and local hardware/thermal settings.

## Stage and review together

Prepare a manifest and diff containing the firmware extension, `sm2_i2c.py`,
`sm2_sensor_health.py`, both sensor drivers, the updated `sm2_connection.py`,
automation/control files, and dashboard/history updates. Change only the four
ambient sensor factories to `SM2_BME280` and `SM2_SGP40`; ordinary sensors keep
their original transport. Never substitute these drivers for heater sensors.

The install must reject a partial or mixed candidate. An old firmware cannot
support these drivers, and the reporting firmware alone does not change the
old sensor drivers' behavior. Preserve output-off and cooldown routes when
sensor readiness is false. Do not enable outputs merely to validate a stage.

## Attended deployment and acceptance

After hardware work is complete and the user is present, verify idle state
again, stop the host, install the matched candidate, flash only the identified
SM MCU, and restart. Check the running SM protocol, four sensor statuses,
warm-up, readiness gate, and unchanged thermal settings before output tests.

Observe normal operation first. Then use a separately reviewed fault-test
procedure with heat-producing outputs disabled. Establish that sensor-bus
errors are reported without a printer shutdown, invalid measurements do not
feed VOC learning or automation, retries stop at their bound, and recovery
does not re-arm UV/Peltier. Verify cooldown and actual output state under
attended conditions before allowing a print. A USB disconnect is a different
test from a sensor I2C failure; passing one does not validate the other.

Record console/log incident counts, failed attempts, categories, observation
duration and host restarts. These are per-sensor session counters, not a count
of independent physical bus faults or a lifetime reliability estimate.

## Rollback

Keep the original firmware and the complete host/config backup together.
Restore the matched original drivers, factories and configuration; restore
firmware if needed, then verify ready state and readings while idle. Do not
leave prototype drivers configured against firmware without their protocol.
Retain diagnostic logs and the candidate manifest for investigation.

Installation tooling, physical output reconciliation and attended hardware
acceptance remain required before this procedure can be treated as a release.
