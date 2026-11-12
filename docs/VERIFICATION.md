# Verification

What is to be exercised against FlashBlade hardware for this part, what is
replayed from fixtures, and what is not verified. The design is in
[DESIGN.md](DESIGN.md); array and tool facts are in
[HARDWARE_NOTES.md](HARDWARE_NOTES.md).

## Environment

A FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21): 10 blades, 40 DFM
drives, one LAG of 8 x 100 GbE to a Cisco Nexus switch with LLDP visible,
one file system (a Manila share, never touched by the tests), no buckets,
six open alerts. Tooling: ansible-rulebook 1.3.x, ansible-core 2.17,
py-pure-client 1.98.0, everpure.flashblade 1.29.0, ansible.eda 2.13.0. An
NFS client VM provides the load and the quota writes. This is a prototype;
it is not a supported product.

## How the tests run

`tests/e2e_sources.sh` runs each rulebook against the array with
`--print-events` for a fixed time and counts the event names, opens and
closes a one-hour maintenance window, replays the hardware fixture to
light and clear an identify LED, and runs the audits signal directly; it
creates, destroys and resizes nothing. The quota, grow and client tests
below need a scratch file system and a load generator and are run by hand
with the playbooks named. Everything the script writes goes under
`tests/out/`. What cannot be induced (a blade fault, a degraded LAG, a
full array, a hot client) is replayed from `tests/fixtures/` through
`ansible.eda.generic`; the replayed events still drive the real actions
on the array.

## Lab exit tests for this part

Every test restores what it changes: the window is closed, the LED
cleared, the scratch file system and its quota eradicated.

| # | Test | Expected | Status |
| --- | --- | --- | --- |
| 1 | `purefb_events` against the lab array, all six signals | `purefb_alert` with transition `initial` for the six open alerts; `purefb_health` with `blade_count` 10 and `drive_count` 40; `purefb_capacity` with `days_to_full_known` true; `purefb_config` with the array's real NTP servers and DNS; no `purefb_poll_error` | not yet run |
| 2 | a scratch 1 GiB file system `eda-demo-quota` with its own export policy, written to 860 MiB from the client VM | alert 1101 opens and `purefb_fs_quota` reports `used_pct` 86; `grow_fs.yml` grows it to 1.25 GiB (`fs_grow_step_pct` 25) and the record carries the old and new size | not yet run |
| 3 | a numeric-uid user quota on that file system, exceeded from the client VM | alert 1106 opens; `user_quota.yml` records the quota and the usage for the file system and user | not yet run |
| 4 | a `purepolicy` change made in the GUI | `purefb_audit` arrives with `user_interface GUI`, `by_automation false` and `automation_agent false`; `change_control.yml` notifies with user, address, interface, command, subcommand and arguments | not yet run |
| 5 | `identify_led.yml` against `CH1.FB1`; then the replayed 1000 fixture through `tests/replay_health.yml` | the LED lights on the real component, the record carries the LLDP neighbour line (switch name and port); the replayed fixture takes the same path; the LED is cleared afterwards | not yet run |
| 6 | fio from the client VM against the scratch file system | `purefb_client_performance` names the client's address with `ops_share_pct` near 100; the client rule stays quiet on the default thresholds and fires with `baseline_latency_ratio` lowered | not yet run |
| 7 | a one-hour maintenance window opened through `maintenance_window.yml` (`action: open, hours: 1`), then closed | `maintenance_active` reads true on the next health, alert and audit events and false again after the close; the human-window rule does not fire for the automation account | not yet run |

## Replayed only

Exercised from fixtures under `tests/fixtures/`, not induced on the array:
a blade fault and a degraded LAG (`tests/replay_health.yml`), the alert
routes (`tests/replay_alerts.yml`), days to full (`tests/replay_capacity.yml`)
and a client's share. The replay rulebooks are kept rule-for-rule identical
to the live ones by the unit test, so a replayed event takes the same path
as a live one.

## Not verified

- The management syslog layouts: `purefb_syslog` parses the lines the
  FlashBlade Admin Guide documents; no syslog server points at the
  listener yet, so the array's actual lines are unconfirmed.
- The data-audit JSON branch of the listener: SMB audit needs an Active
  Directory join the lab lacks and NFS audit needs Purity//FB 4.8.0.
- A real hardware fault, a hot array, a full array: replayed or induced on
  lowered thresholds only.
- The config drift reapply against a drifted NTP or DNS setting, and the
  reconcile from the audit record: the plugin and the playbooks are
  unit-tested; the live run is part of the change-control exit test when
  it is extended to NTP.
- The QoS cap and lift (`qos_cap.yml`, `qos_lift.yml`): written against
  `/qos-policies` and `patch_file_systems qos_policy` through the raw REST
  task; not yet run on an array.
- A maintenance window older than REST 2.16: the gate reads false there by
  design and no such array is in the lab.
- Scale beyond one array; the 4.5.6 peer is read only by later parts.
- The object-audit, telemetry, containment and replication work of the
  later parts ([DESIGN.md](DESIGN.md), "Roadmap").
