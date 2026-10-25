# I2C candidate rollout requirements

The candidate remains uninstalled. This document defines the installation
contract; it is not authorization to flash a board or a completed installer.

## Read-only baseline audit

`scripts/preflight_sm2_i2c.py` is the first part of preflight. Run it from a
complete candidate checkout on the printer host; it needs the bundled source
fixtures. It only performs local Moonraker GET requests, reads source files,
and runs `git rev-parse`. It prints JSON to standard output and exits nonzero
on missing or mismatched evidence. It has no install or restart option.

```sh
python3 scripts/preflight_sm2_i2c.py --klipper-path "$HOME/klipper"
```

It checks ready/idle/print state, confirmed UV/Peltier off, no pending config
save, the running host path/version, resolved installed BME/SGP/GIA source,
repository revisions, and the four sensor definitions returned by Kalico.
It deliberately accepts only the original baseline factories and the
development printer's bus layout. Mixed/previously installed prototypes and
additional SM I2C devices require review instead of automatic acceptance.

`baseline_checks_passed: true` is evidence for staging only.
`installation_allowed` is always false. This baseline script does not resolve the
on-disk include tree, prove that files match the loaded runtime, identify the
running firmware protocol, verify a recovery bundle, or establish hardware
safety. A saved report is not proof that a printer remains idle later.

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

The configuration-only staging tool is now available:

```sh
python3 scripts/stage_sm2_config.py \
  --config "$HOME/printer_data/config/printer.cfg" \
  --runtime-config /path/to/runtime-config.json \
  --output "$HOME/sm2-config-stage-NEW"
```

The runtime JSON must contain the `configfile.config` object from a fresh
Moonraker query, not the surrounding response. The tool resolves ordinary
nested includes and sorted globs, records the source of each target section,
and compares all four sensors' transport/reference/factory settings to that
runtime object. It rejects missing, repeated, recursive or parameterized
includes, inline includes, duplicate target sections and partial migrations.
These intentional restrictions require manual review of unfamiliar layouts;
this is not a replacement for Kalico's full configuration parser.

It writes byte-for-byte copies of every visited config file, separate candidate
files, a unified diff, and a checksum manifest into a new directory. The four
sensor factories change, and `[sm2_bme280]` / `[sm2_sgp40]` registration sections
are prepended to the root config so they load before the sensor sections.
Existing comments, line endings and SAVE_CONFIG data stay intact. Old stages
without registration must be rebuilt. The destination must be outside active config directories and include
glob search roots. Existing directories are never overwritten. Backup and
candidate checksums are read back, and the include tree is inspected again
before the completion manifest is written. A directory without a manifest is
an incomplete stage and must not be used for installation.

The source configuration is never modified. The manifest's scope is
`configuration-only`, and `installation_allowed` remains false. It does not
back up firmware, modules, external macro snippets or unrelated state files;
it does not prove the supplied runtime snapshot is current. Complete those
recovery checks and rerun live preflight before any deployment.

Prepare a manifest and diff containing the firmware extension, `sm2_i2c.py`,
`sm2_sensor_health.py`, both sensor drivers, the updated `sm2_connection.py`,
automation/control files, and dashboard/history updates. Change only the four
ambient sensor factories to `SM2_BME280` and `SM2_SGP40`; ordinary sensors keep
their original transport. Never substitute these drivers for heater sensors.

The install must reject a partial or mixed candidate. An old firmware cannot
support these drivers, and the reporting firmware alone does not change the
old sensor drivers' behavior. Preserve output-off and cooldown routes when
sensor readiness is false. Do not enable outputs merely to validate a stage.

### Host-file deployment and rollback plan

`scripts/prepare_sm2_deployment.py --recovery RECOVERY --candidate CHECKOUT
--output NEW_DIRECTORY` verifies the recovery bundle and creates a separate
review directory. It includes sensor-selection/registration edits, all active
automation config candidates, five matching SM2 host modules, and the two
dashboard/history files. Each existing destination must have recovery evidence;
new destinations must appear as originally absent in the module inventory.
Missing/duplicate active macro files and overlapping edits require review.

The plan contains forward and rollback payloads with checksums, destination and
resolved paths, original link/permission metadata where captured, and a unified
diff. Review every macro diff: whole-file macro candidates may replace local
customizations and are not automatically approved merges. Configuration copies
alone do not preserve the permissions/link topology of every original include;
that evidence must be completed before implementing an automatic restore.

This command never applies files, changes services, or flashes firmware. Its
`installation_allowed` field remains false. A stopped-host apply/rollback
executor, verification of matching SM firmware and attended acceptance are still
required. Do not treat a generated plan as an executable install script.

## Attended deployment and acceptance

### Recovery bundle tooling

On the printer host, a passing baseline audit can write a new private runtime
snapshot with `--evidence-output /path/to/runtime-evidence.json`. This contains
the full loaded configuration; keep it with private backups rather than
committing it to a public repository. Existing evidence files are not overwritten.

`scripts/bundle_sm2_recovery.py` accepts `--klipper`, `--automation`, `--config`,
`--firmware`, `--evidence`, and `--output` paths. The firmware path must contain
the saved original SM build's `.config` and `out/klipper.bin`, `klipper.elf`, and
`klipper.dict`. The automation path must identify the installed project, not a
separate unused clone. The config path must match the host's reported root.

The tool reruns the baseline checks against the supplied snapshot, stages the
config copies/diff, and captures the installed BME/SGP/GIA/I2C sources,
dashboard/history code, saved-variable file, existing SM2 modules and original
firmware files. Its inventory records which prototype module paths were absent,
and records resolved source paths, direct symlink targets and file permissions
for existing files. Copies are verified and sources rechecked before writing
the completion manifest. The destination must be new and outside source trees.

The saved firmware dictionary must match the snapshot's SM firmware version
and G0B1 identity; build settings must match the reviewed 8 MHz USB/no-offset
profile. These checks do **not** prove the saved binary is the flashed image.
There is no MCU flash readback or claim that arbitrary binary/dictionary pairs
are authentic. Preserve the original known-good build artifacts.

After copying the directory to the PC, check its stored files with:

```sh
python3 scripts/verify_sm2_recovery.py /path/to/recovery-directory
```

This verifies artifact, runtime-evidence and configuration checksums and rejects
paths escaping the bundle. It does not restore files or grant installation
permission. It is a migration-file recovery set, not a full OS/Python environment
backup. Restore instructions must use the recorded link targets and permissions;
do not blindly replace a symlink with a regular file or delete pre-existing modules.

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
