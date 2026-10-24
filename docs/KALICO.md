# Kalico development and releases

## Repository separation

- Klipper releases: https://github.com/dyocis/SM2_PCBv3_automation
- Kalico releases: https://github.com/dyocis/SM2_PCBv3_automation_kalico
- Baseline: upstream v0.4.0, commit d1ff6fb5844ae6a4b0dfe46835ec0e3661237c07.
- Preserve the upstream Git history, license, and credits. Do not republish
  upstream release tags as Kalico releases.
- Work on `development`, using focused branches and reviewed changes. `main`
  currently contains the experimental baseline; it becomes the release branch
  after the first Kalico candidate passes validation. No stable Kalico release
  exists until then.
- Apply shared fixes to each project deliberately; neither project's update
  manager should silently switch users to the other firmware family.

## Compatibility work

The initial target is Kalico commit
`3ec85e6aae670182955cf9f4cdc968cf1fba1315`, with matching SM MCU firmware.
Record tested host, MCU firmware, SGP40 driver and LED-effect versions for each
release. A newer Kalico `main` commit is not automatically a supported target.

1. Consolidate configuration. The upstream v0.4.0 `SM2_Control.cfg` already
   includes the fan/UV idle-timeout fix. Compare any local
   `idle_timeout_fix.cfg` overrides before removing their include. Preserve
   local hardware settings, saved calibration, and print macro integration.
2. Handle non-critical SM disconnection across macros, delayed work, LED
   callbacks, sensor drivers, and dashboard/history. Do not expose stale
   measurements as current or send commands to unavailable hardware.
3. Add recoverable I2C error handling where the supported Kalico host/firmware
   and sensor drivers allow it. `is_non_critical` alone does not suppress a
   firmware shutdown caused by an I2C error. Do not globally suppress shutdowns
   or disable thermistor/thermal protections.
4. Define safe reconnect behavior and output state reconciliation. Recovery
   must not restart UV/Peltier using stale temperatures or cached requests.
5. Update installer preflight checks, dependency versions, updater source,
   migration/rollback instructions, and dashboard availability reporting.

## Stable release criteria

Run existing configuration, installer, dashboard, idle-timeout and thermal
tests, plus failure/recovery regression tests. Document attended hardware
validation for initial absence, disconnect, reconnect, I2C errors and stale
sensor data, including safe outputs and impact on the rest of the printer.
Start fault tests with heat-producing outputs disabled and validate each
hardware safety case deliberately. Simulation is not proof of hardware safety.

Publish only after the supported matrix and remaining limitations are written
in release notes. Use this repository's independent `vX.Y.Z` tags; the first
Kalico release should be v0.1.0. Never push upstream tags here. The inherited
tag workflow runs checks and publishes an archive/checksum, but passing CI
alone is not approval to tag a stable release.

Keep the working printer on its existing configuration during development.
Migration must be explicit, backed up, and performed while idle. A host-only
rollback may be insufficient if firmware or sensor protocols have changed.
