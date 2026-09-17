---
name: eks-noc-dashboard
description: Deploy, validate and customize the Amazon EKS NOC dashboard sample (CloudWatch PromQL + Application Signals). Use when the user wants to set this NOC up in an account, point it at a different cluster or services, add or change widgets, explain a blank or EMPTY widget, or extend it across accounts and Regions.
metadata:
  source: aws-samples/sample-code-for-noc-dashboard-on-eks-with-cloudwatch
  version: "1.0"
---

# EKS NOC dashboard: agent guide

You are helping a reader deploy or customize the dashboards in this folder. The reader should not
need to read Python, PromQL or dashboard JSON. Read this file, then act; only open the source files
that a change actually touches.

## What is here

| File | Purpose |
|---|---|
| `config.py` | The ONLY file with environment values: Region, account, cluster, service to namespace map, backing services, alarm names. Every script imports it. |
| `noc_dashboard.py` | Builds 4 dashboards (NOC + 3 drilldowns) and writes `<name>_body.json` plus `_promql`, `_classic` and `_logs` manifests. |
| `probe.py` | Validates the manifests against live telemetry (OK / EMPTY / FAILED per plane). `--discover` lists the Application Signals services CloudWatch has actually seen. |
| `alarms.sh` | Three alarms: Application Signals heartbeat, OTel heartbeat, node not Ready. |
| `deploy.sh` | build, probe, PutDashboard, GetDashboard round trip. |
| `cleanup.sh` | Deletes dashboards and alarms. |
| `tests/test_build.py` | Offline tests; run after any change to the builder or config. |
| `docs/patterns.md` | Why the queries look the way they do. Read this before changing a query. |
| `docs/multi-account-multi-region.md` | Fleet options: OAM, per-query Region/account, Metrics centralization. |

## Deploy for the reader (happy path)

1. Confirm prerequisites: an EKS cluster with the `amazon-cloudwatch-observability` add-on
   (validated with v6.4.0-eksbuild.1) using `addon-config.json` (OTel Container Insights and
   Application Signals on), Python 3.12+, AWS CLI with credentials for the target account and Region.
2. Edit `config.py`: `REGION`, `ACCOUNT`, `CLUSTER`, and `SERVICE_NAMESPACES`. Ask the reader for
   the Kubernetes namespace of each service if you cannot read it with `kubectl get deploy -A`.
   Set `ALB`, `TARGET_GROUP` and `DDB_TABLE` to `None` when the reader has no such resources.
3. Run `python3 probe.py --discover`. Every configured service must appear with the derived
   Environment (`eks:<cluster>/<namespace>`). If one is MISSING, fix the namespace, do not deploy.
4. Run `python3 -m unittest discover -s tests`, then `./alarms.sh` (replace the SNS topic first),
   then `./deploy.sh`.
5. Tell the reader to open the NOC in the console once. PromQL chart widgets cannot be rendered
   through an API, so the console is the only proof that every widget draws.

## Rules that keep the dashboards trustworthy

- Never hand-edit a `_body.json`. Change `config.py` or `noc_dashboard.py`, rebuild, re-probe.
- Every PromQL query must include the cluster selector `CL`. Use the `Canvas` helpers; do not
  write raw widget JSON.
- Counters and state conditions are zero-filled (`zero_filled()` / `FILL(m,0)`). Latency and
  utilization metrics are never zero-filled: a filled 0 ms asserts a measurement that did not happen.
- Application Signals `Environment` on EKS is `eks:<cluster>/<namespace>`, not the service name.
  Always go through `config.env()`.
- Treat probe `FAILED` as blocking. Treat `EMPTY` as a workload condition to explain (no traffic,
  wrong dimension, Auto Mode removed the resource), never as success.
- Keep widget widths that divide into 24 so rows stay aligned when sections stack on the NOC.
- Add drilldown-only widgets after the `if not detail: return` line of the owning section, so the
  NOC and its drilldown keep sharing the condensed queries.
- Do not use the `__verbose__` label; use `join_labels()` for multi-label legends.

## Common customizations (what to change)

| Reader asks | Do this |
|---|---|
| Point it at my cluster / services | Edit `config.py` only. Rebuild, `--discover`, probe, deploy. |
| Add a service | Add it to `SERVICE_NAMESPACES` (and `JVM_SERVICES` if Java). Nothing else. |
| Add a widget for metric X | Decide the plane first (PromQL store vs CloudWatch namespace, see `docs/patterns.md`). Use `c.chart()` for PromQL, `c.metric()` for CloudWatch metrics, in the right section. Re-run tests and probe. |
| Change SLO line / thresholds | `h_annotation()` values in `app_health()` and `infrastructure()`. |
| Add an alarm to the NOC alarm widget | Create it (PromQL or classic), append its name to `ALARM_NAMES` in `config.py`. |
| Different time window | `Canvas(start="-PT6H")` in `build_all()`; ISO-8601 durations. |
| Multi-account or multi-Region | Read `docs/multi-account-multi-region.md` first; per-query `region` / `accountId` on chart widgets, or Metrics centralization for one PromQL expression across the fleet. |
| A widget is blank | Run the probe for that dashboard. EMPTY = no samples for that selector and window (check namespace mapping, traffic, Auto Mode scale-in). FAILED = query or dimension error. Blank with OK = console time range. |

## Verify before you say "done"

- `python3 -m unittest discover -s tests` passes.
- `python3 probe.py <dashboard> 180` shows FAILED=0 for every dashboard and you can explain each EMPTY.
- `deploy.sh` printed `DEPLOYED_MATCHES_BUILD=True`.
- Report the `BODY_SHA256` and `ADDON_VERSION_TESTED` lines with your summary.
