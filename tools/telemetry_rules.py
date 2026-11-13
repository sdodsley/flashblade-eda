#!/usr/bin/env python3
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Write prometheus/flashblade.rules.yml from a FlashBlade's own listing of
its telemetry metric policies. Standard library only.

    python tools/telemetry_rules.py fb-a.example.com FB_A_TOKEN
    python tools/telemetry_rules.py --from-file tests/fixtures/telemetry_rules.json --array-name fb-a
    python tools/telemetry_rules.py ... --check      # exit 1 when the committed file differs

The array pushes OTLP metrics into Prometheus's OTLP receiver; nothing is
scraped, so there is no `up == 0` to say that an array has gone quiet.
Silence is the signal: for every telemetry-metrics policy in
GET /telemetry-metrics-policies/rules (REST 2.21, Purity//FB 4.6.4) one
FlashBladeTelemetrySilent alert fires when the policy's first metric has
been absent for twice the policy's resolution or --silence-minutes,
whichever is longer, so a 10 minute policy waits 20 minutes and a 1 minute
policy waits the 10 minute floor. `absent_over_time` keeps only the labels
its selector pins, which is why each silence rule names one array
(--array-name, the value the array pushes in its device label; read from
/arrays when the listing comes from the array) and why a receiver that
sees several arrays needs the option once per array.

The hand-written rules (health, capacity, latency, connector errors,
replication) are HANDWRITTEN below and are appended unchanged. Every alert
carries severity, domain and array_name, the last templated from the array
label (--array-label, default device_name) so that the Alertmanager
rulebook can read one fixed label name. Lines marked `verify:` rest on
facts no receiver has confirmed yet: the exact Prometheus names after the
UnderscoreEscapingWithSuffixes translation, the name of the resource
attribute carrying the array name, and the attributes of the space and
health series.

The token is read from the environment variable named on the command line
and is never printed; the array is read with the session login the
playbooks use (POST /api/login with the api-token header, x-auth-token on
every request, POST /api/logout at the end). The output is deterministic
(policies and arrays sorted), so the committed file is checked against a
fixture listing by tests/unit/test_telemetry_rules.py.
"""

import argparse
import json
import math
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "prometheus" / "flashblade.rules.yml"
DEFAULT_SILENCE_MINUTES = 10
DEFAULT_ARRAY_LABEL = "device_name"
# the REST version that first lists the telemetry metric policies
MIN_API_VERSION = (2, 21)
PAGE_LIMIT = 200
TIMEOUT_SECONDS = 60
GENERATOR = "tools/telemetry_rules.py"
SILENCE_ALERT = "FlashBladeTelemetrySilent"
HANDWRITTEN_ALERTS = (
    "FlashBladeHealthNotOk",
    "FlashBladeCapacityHigh",
    "FlashBladeLatencyHigh",
    "FlashBladeConnectorErrors",
    "FlashBladeReplicationStalled",
)


class RulesError(Exception):
    """A failure the tool reports; the message never holds the token."""


# ------------------------------------------------------------- the listing
def policies_from_rules(items):
    """Group the rules listing by policy: name -> {"metrics": [metric names
    in index order], "resolution_ms": the first metric's resolution}. The
    first metric is the one the silence rule watches, so its resolution is
    the one that sets the window; 4.8.6 makes the resolution per rule."""
    by_policy = {}
    for item in items:
        policy = (item.get("policy") or {}).get("name") or item.get("name", "").rsplit(
            ".", 1
        )[0]
        if not policy or not item.get("metric_name"):
            continue
        by_policy.setdefault(policy, []).append(
            (
                int(item.get("index") or 0),
                item["metric_name"],
                int(item.get("resolution") or 0),
            )
        )
    out = {}
    for name in sorted(by_policy):
        rules = sorted(by_policy[name])
        out[name] = {
            "metrics": [metric for _, metric, _ in rules],
            "resolution_ms": rules[0][2],
        }
    return out


def prometheus_name(metric_name):
    """The OTLP dotted name as the receiver's UnderscoreEscapingWithSuffixes
    strategy stores it: every character outside [a-zA-Z0-9_:] becomes an
    underscore. A unit suffix is appended only for a metric that carries a
    unit; none of the policy metrics is assumed to (verify)."""
    return "".join(c if (c.isalnum() or c in "_:") else "_" for c in metric_name)


def silence_window_minutes(resolution_ms, floor_minutes):
    """Twice the resolution, rounded up to whole minutes, or the floor."""
    return max(math.ceil(2 * resolution_ms / 60000), floor_minutes)


# ------------------------------------------------------------- the output
HEADER = """\
---
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Prometheus alerting rules for a FlashBlade that pushes OTLP metrics into
# Prometheus's OTLP receiver (Purity//FB 4.6.4, REST 2.21; prometheus/README.md
# says how). Generated by @GENERATOR@ from the array's own
# telemetry-metrics-policies rules listing: @POLICIES@ policies, @METRICS@ metrics;
# array label @LABEL@; silence floor @FLOOR@ minutes; arrays @ARRAYS@. Regenerate
# rather than edit the silence rules; the hand-written rules at the end are
# the template in the script.
#
# Nothing is scraped, so there is no `up == 0`: silence is the signal. For
# every policy one FlashBladeTelemetrySilent alert fires when the policy's
# first metric has been absent for twice the policy's resolution or the
# floor, whichever is longer. absent_over_time keeps only the labels its
# selector pins, so each silence rule names one array. Alertmanager hands
# every alert to ansible.eda.alertmanager; extensions/eda/rulebooks/alertmanager.yml
# routes by alertname and reads array_name, which every alert sets from
# the array label. Thresholds are deliberately conservative; tune them per
# site. FlashBladeCapacityHigh puts the bare percentage in annotations.value
# for the rulebook; summary is the sentence for humans.
#
# Verified against a Prometheus 3.16 OTLP receiver on 2026-11-13: the metric
# names are the listing's dotted names with underscores (storage.device.iops
# -> storage_device_iops, the UnderscoreEscapingWithSuffixes strategy, no
# unit suffix); @LABEL@ is the resource attribute that names the array,
# promoted to a label by promote_all_resource_attributes (device_id comes
# with it); the space series carry space_type (capacity, total_physical,
# total_provisioned, available_provisioned, unique, virtual, snapshots,
# shared, destroyed, destroyed_virtual) and storage_type (all, file, object);
# the performance series carry op, proto, resource_type and storage_type; the
# health series one per component with component_name and component_type.
groups:
  - name: flashblade-telemetry-silence
    rules:
"""

SILENCE_RULE = """\
      - alert: FlashBladeTelemetrySilent
        # @POLICY@: @COUNT@ metric@PLURAL@ every @RESOLUTION@ s, watched through @METRIC@;
        # the window is twice the resolution or the @FLOOR@ minute floor
        expr: absent_over_time(@SERIES@{@LABEL@="@ARRAY@"}[@WINDOW@m])
        labels:
          severity: critical
          domain: telemetry
          policy: @POLICY@
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} has pushed nothing for policy @POLICY@ (@SERIES@) in @WINDOW@ minutes: dead array, dead network, a disabled target or a receiver fault"
"""

HANDWRITTEN = """\

  - name: flashblade-eda
    rules:
      # --- health ---------------------------------------------------------------
      - alert: FlashBladeHealthNotOk
        # the gauge is the component status as an integer; the receiver saw 5
        # for every healthy component on Purity//FB 4.6.4 and the enum is not
        # published, so any other value alerts (verify: on a fault)
        expr: storage_device_health != 5
        for: 5m
        labels:
          severity: critical
          domain: health
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} {{ $labels.component_name }} ({{ $labels.component_type }}) reports health {{ $value }}"

      - alert: FlashBladeCriticalAlertOpen
        # one series per open alert on the array, valued with the alert code and
        # labelled with its severity, summary, component and knowledge base link
        expr: storage_device_alerts{severity="critical"}
        labels:
          severity: critical
          domain: alerts
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} alert {{ $value }}: {{ $labels.summary }}"
          value: "{{ $value }}"

      # --- capacity -------------------------------------------------------------
      - alert: FlashBladeCapacityHigh
        # the array-wide series (storage_type all): total_physical used against
        # capacity, the same two fields as REST /arrays/space
        expr: |
          100 * storage_device_space_bytes{space_type="total_physical", storage_type="all"}
            / ignoring (space_type) storage_device_space_bytes{space_type="capacity", storage_type="all"} > 85
        for: 15m
        labels:
          severity: warning
          domain: capacity
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} is {{ $value | printf \\"%.1f\\" }}% full"
          value: "{{ $value | printf \\"%.1f\\" }}"

      # --- performance ----------------------------------------------------------
      - alert: FlashBladeLatencyHigh
        # 20 ms sustained for 10 minutes on the array-wide series (all protocols,
        # all resources), one alert per op (read, write, other)
        expr: storage_device_latency_us{proto="all", resource_type="all", storage_type="all"} > 20000
        for: 10m
        labels:
          severity: warning
          domain: performance
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} {{ $labels.op }} latency is {{ $value | printf \\"%.0f\\" }} us"

      # --- network --------------------------------------------------------------
      - alert: FlashBladeConnectorErrors
        # err_ps is already a per-second rate (a gauge), so the 5 minute average
        # rather than rate(), which expects a counter; the connector's own
        # attributes arrive as labels on the alert. The lab's ports report a
        # steady "other" count (16/s on busy uplinks) that is not a fault, so
        # only the named error kinds (crc, frame, ...) count
        expr: avg_over_time(storage_device_connector_err_ps{err!~"total|other"}[5m]) > 0
        for: 10m
        labels:
          severity: warning
          domain: health
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} has a connector erroring at {{ $value | printf \\"%.2f\\" }}/s over 5 minutes"

      # --- replication ----------------------------------------------------------
      - alert: FlashBladeReplicationStalled
        # only meaningful on an array with a replica link; an array that
        # replicates nothing pushes zero (or nothing) and should silence this
        expr: storage_device_repl_bw_bps == 0
        for: 30m
        labels:
          severity: warning
          domain: replication
          array_name: "{{ $labels.@LABEL@ }}"
        annotations:
          summary: "{{ $labels.@LABEL@ }} has replicated nothing for 30 minutes"
"""


def render(policies, array_names, silence_minutes, array_label):
    """The rule file as text. Policies and arrays are sorted so the same
    listing always gives the same file."""
    arrays = sorted(set(array_names))
    if not arrays:
        raise RulesError("at least one --array-name is needed for the silence rules")
    if not policies:
        raise RulesError("the listing holds no telemetry metric policies")
    metric_count = sum(len(p["metrics"]) for p in policies.values())
    out = [
        _fill(
            HEADER,
            GENERATOR=GENERATOR,
            POLICIES=str(len(policies)),
            METRICS=str(metric_count),
            LABEL=array_label,
            FLOOR=str(silence_minutes),
            ARRAYS=", ".join(arrays),
        )
    ]
    rules = []
    for name, policy in policies.items():
        # the per-client policies push nothing while no client is active, so
        # their absence is not silence; they get no rule
        if "client" in name:
            continue
        metric = policy["metrics"][0]
        window = silence_window_minutes(policy["resolution_ms"], silence_minutes)
        for array in arrays:
            rules.append(
                _fill(
                    SILENCE_RULE,
                    POLICY=name,
                    COUNT=str(len(policy["metrics"])),
                    PLURAL="" if len(policy["metrics"]) == 1 else "s",
                    RESOLUTION=str(policy["resolution_ms"] // 1000),
                    METRIC=metric,
                    SERIES=prometheus_name(metric),
                    LABEL=array_label,
                    ARRAY=array,
                    WINDOW=str(window),
                    FLOOR=str(silence_minutes),
                )
            )
    out.append("\n".join(rules))
    out.append(_fill(HANDWRITTEN, LABEL=array_label))
    return "".join(out)


def _fill(template, **values):
    """@KEY@ substitution: the templates hold Prometheus's own {{ }} and $,
    so neither str.format nor string.Template can touch them."""
    for key, value in values.items():
        template = template.replace(f"@{key}@", value)
    return template


# ------------------------------------------------------------- the array
class Array:
    """The few REST calls the tool makes, with the playbooks' session login.
    fb_url is the management address as the collection modules take it
    (host or IP, no scheme); a full https:// URL is accepted as well."""

    def __init__(self, fb_url, token, verify_certs, api_version=None):
        base = fb_url.strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            base = "https://" + base
        self.base = base
        self.token = token
        self.context = (
            ssl.create_default_context()
            if verify_certs
            else ssl._create_unverified_context()
        )
        self.session = None
        # a version given on the command line skips the negotiation
        self.version = api_version

    def _request(self, method, path, headers=None, body=None):
        request = urllib.request.Request(
            self.base + path, data=body, method=method, headers=headers or {}
        )
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT_SECONDS, context=self.context
            ) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()
        except urllib.error.URLError as err:
            raise RulesError(f"{method} {path} on {self.base}: {err.reason}") from None
        except OSError as err:
            raise RulesError(f"{method} {path} on {self.base}: {err}") from None

    def _json(self, method, path):
        headers = {"x-auth-token": self.session} if self.session else {}
        status, _, body = self._request(method, path, headers)
        try:
            parsed = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            parsed = {}
        if status != 200:
            errors = parsed.get("errors") if isinstance(parsed, dict) else None
            detail = (
                errors[0].get("message", "")
                if isinstance(errors, list) and errors
                else f"status {status}"
            )
            raise RulesError(f"{method} {path} on {self.base}: {detail}")
        return parsed

    def login(self):
        """Negotiate the REST version unless one was given, then open a
        session: POST /api/login with the api-token header answers with an
        x-auth-token header."""
        if not self.version:
            versions = self._json("GET", "/api/api_version").get("versions") or []
            parsed = sorted(
                tuple(int(p) for p in v.split(".")) for v in versions if _is_version(v)
            )
            if not parsed or parsed[-1] < MIN_API_VERSION:
                raise RulesError(
                    f"{self.base} serves REST {versions[-1] if versions else 'nothing'};"
                    " the telemetry metric policies need 2.21 (Purity//FB 4.6.4)"
                )
            self.version = ".".join(str(p) for p in parsed[-1])
        status, headers, _ = self._request(
            "POST", "/api/login", {"api-token": self.token}
        )
        if status != 200 or not headers.get("x-auth-token"):
            raise RulesError(
                f"login to {self.base} failed with status {status}: check the token "
                "the environment variable holds"
            )
        self.session = headers.get("x-auth-token")

    def logout(self):
        if self.session:
            try:
                self._request("POST", "/api/logout", {"x-auth-token": self.session})
            except RulesError:
                pass
            self.session = None

    def items(self, resource, **query):
        """Every item of a listing, following continuation tokens."""
        out = []
        token = None
        while True:
            params = dict(query, limit=PAGE_LIMIT)
            if token:
                params["continuation_token"] = token
            path = f"/api/{self.version}/{resource}?{urllib.parse.urlencode(params)}"
            page = self._json("GET", path)
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

    def name(self):
        arrays = self.items("arrays")
        if not arrays or not arrays[0].get("name"):
            raise RulesError(f"{self.base} did not name itself in GET /arrays")
        return arrays[0]["name"]


def _is_version(text):
    parts = text.split(".")
    return len(parts) == 2 and all(p.isdigit() for p in parts)


def read_array(fb_url, token_env, verify_certs, api_version=None):
    """The rules listing and the array's name from a live array. The token
    is the value of the environment variable token_env, never printed."""
    token = os.environ.get(token_env, "")
    if not token:
        raise RulesError(f"the environment variable {token_env} is not set")
    array = Array(fb_url, token, verify_certs, api_version)
    array.login()
    try:
        return array.items("telemetry-metrics-policies/rules"), array.name()
    finally:
        array.logout()


def read_file(path):
    """A saved listing: the REST answer ({"items": [...]}) or a bare list."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RulesError(f"cannot read {path}: {err}") from None
    if isinstance(data, dict):
        data = data.get("items")
    if not isinstance(data, list):
        raise RulesError(f"{path} is neither a rules listing nor a list of rules")
    return data


# ------------------------------------------------------------- the command
def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=GENERATOR,
        description="Write the Prometheus rule file from a FlashBlade's telemetry "
        "metric policy listing.",
    )
    parser.add_argument(
        "fb_url", nargs="?", help="the array's management address (no scheme)"
    )
    parser.add_argument(
        "token_env",
        nargs="?",
        help="name of the environment variable that holds the API token",
    )
    parser.add_argument(
        "--from-file",
        metavar="JSON",
        help="read the rules listing from this file instead of the array",
    )
    parser.add_argument(
        "--array-name",
        action="append",
        default=[],
        metavar="NAME",
        help="value of the array label the array pushes; once per array "
        "(default: the array's name from GET /arrays; required with --from-file)",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="the rule file to write (default prometheus/flashblade.rules.yml; "
        "- for stdout)",
    )
    parser.add_argument(
        "--silence-minutes",
        type=int,
        default=DEFAULT_SILENCE_MINUTES,
        help="floor of the silence window (default %(default)s)",
    )
    parser.add_argument(
        "--array-label",
        default=DEFAULT_ARRAY_LABEL,
        help="the label that names the array on every series (default %(default)s)",
    )
    parser.add_argument(
        "--api-version",
        help="REST version to use (default: the newest the array serves)",
    )
    parser.add_argument(
        "--verify-certs",
        action="store_true",
        help="check the array's certificate (off by default: lab arrays are "
        "self-signed)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with --out instead of writing; exit 1 when it differs",
    )
    args = parser.parse_args(argv)
    if args.from_file:
        if args.fb_url or args.token_env:
            parser.error("--from-file takes no array address or token variable")
        if not args.array_name:
            parser.error("--from-file needs --array-name")
    elif not (args.fb_url and args.token_env):
        parser.error("give the array address and the token variable, or --from-file")
    if args.silence_minutes < 1:
        parser.error("--silence-minutes must be at least 1")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.from_file:
            items = read_file(args.from_file)
            arrays = args.array_name
        else:
            items, own_name = read_array(
                args.fb_url, args.token_env, args.verify_certs, args.api_version
            )
            arrays = args.array_name or [own_name]
        policies = policies_from_rules(items)
        text = render(policies, arrays, args.silence_minutes, args.array_label)
    except RulesError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    summary = (
        f"{len(policies)} policies, {sum(len(p['metrics']) for p in policies.values())} "
        f"metrics, {len(set(arrays))} array{'s' if len(set(arrays)) != 1 else ''}"
    )
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    out = pathlib.Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else None
        if current == text:
            print(f"{out}: unchanged ({summary})")
            return 0
        print(f"{out}: {'differs' if current is not None else 'missing'} ({summary})")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"wrote {out}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
