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

### Connection gate development candidate

The first candidate adds `[sm2_connection]` and an installer-owned host-module
symlink. It requires `[mcu SM] is_non_critical: True`. Existing hardware files
are preserved, so their setting must be checked explicitly during migration.
The monitor reads live MCU flags and listens for Kalico disconnect/reconnect
events. The current target's exported MCU status may lag the actual disconnect.

Control macros, startup work, calibration work, and delayed callbacks check this
gate before evaluating sensor readings or scheduling hardware commands. A loss
of connection latches the gate; reconnection does not re-arm it. Inspect the
hardware and restart Kalico while idle to create a fresh monitor. There is no
runtime override/reset command. The `sm2_connection` status object reports
availability, connection state, restart requirement, and reason.

This is an uninstalled software-test candidate, not a complete fault-tolerance
implementation. It does not cancel commands already rendered/queued, ensure
physical outputs shut off, stop third-party sensor/LED callbacks, establish
sensor freshness, or change firmware shutdown behavior. Existing status caches
and dashboard history still need validity handling. Initial MCU absence may
still prevent sensor-driver startup before the gate can act. Direct `SET_PIN`,
`SET_FAN_SPEED`, `SET_SERVO`, and sensor commands bypass the macro gate.
Even an emergency macro is skipped when the gate is inhibited: that cannot be
treated as a physical emergency stop. Resolve these limitations and validate
the output shutdown/cooldown behavior before deployment or fault injection.

Tests cover lifecycle latching and render every shipped control template with
no sensor/output objects and an absent or unavailable monitor. Existing thermal
and automation tests continue to exercise the connected paths. These are
software checks only.

### Release validation

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
