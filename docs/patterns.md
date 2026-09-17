# Why the queries look the way they do

This page holds the detail that the blog post keeps out of the way. You do not need it to deploy
the dashboards. Read it when you change a query, or ask your AI assistant to apply the rule for you.

Validated against the `amazon-cloudwatch-observability` add-on **v6.4.0-eksbuild.1** on Amazon
EKS Auto Mode (Kubernetes 1.34). The metric names (`k8s.*`, `kube_*`, `apiserver_*`) and the
histogram representation noted in section 6 can change between add-on versions. Re-run
`probe.py` after every add-on upgrade.

## 1. One signal, one plane

A widget that queries the wrong plane returns nothing, and the failure is silent. Decide the plane
before writing a widget.

| Signal | Store | Query language | Builder call |
|---|---|---|---|
| Node, pod, namespace, container (`k8s.*`) | PromQL store (OTLP) | PromQL | `c.chart()` / `c.number()` |
| Kubernetes object state (`kube_*`) | PromQL store (OTLP) | PromQL | `c.chart()` / `c.number()` |
| Control plane (`apiserver_*`, `etcd_*`) | PromQL store (OTLP) | PromQL | `c.chart()` |
| Golden signals (Latency, Fault, Error) | `ApplicationSignals` namespace | Metric math | `c.metric()` |
| Ingress, databases, cache, queue | `AWS/ApplicationELB`, `AWS/RDS`, `AWS/DynamoDB`, `AWS/ElastiCache`, `AWS/AmazonMQ` | Metric math | `c.metric()` |
| Log triage | CloudWatch Logs Insights | Logs Insights | `c.log()` |

PromQL chart widgets and classic metric widgets use different JSON for axes and annotations
(`title` vs `label`, `type: static` accepted vs rejected). `Canvas` normalizes both; do not write
widget JSON by hand.

## 2. Scope every PromQL query to the cluster

The PromQL store exposes the cluster as a resource label. Without it, a second cluster in the same
account silently pollutes every number. The builder defines the selector once (`CL`) and the tests
fail if any query lacks it. Dotted OpenTelemetry metric names must be quoted.

```promql
# selector appended to every query
"@resource.k8s.cluster.name"="retail-store"

# node CPU utilization (dotted OTel name is quoted)
avg by ("@resource.k8s.node.name")
  ({"k8s.node.cpu.utilization","@resource.k8s.cluster.name"="retail-store"}) * 100
```

## 3. Application Signals: Service + Environment

Application Signals identifies a service by two dimensions. On EKS the default `Environment` is
`eks:<cluster-name>/<namespace>`, where namespace is the **Kubernetes namespace the pods run in**.
It is not the service name. The retail store sample deploys `ui` into namespace `ui`, `carts` into
`carts` and so on, so the two coincide there and a probe run cannot tell them apart. In your
estate they usually differ, and a wrong Environment does not error: every Application Signals
widget renders EMPTY.

`config.SERVICE_NAMESPACES` maps each service to its namespace and `config.env()` derives the
value. `python3 probe.py --discover` lists the `(Service, Environment)` pairs Application Signals
has actually recorded and flags any configured service that does not match. If a service sets
`deployment.environment` through `OTEL_RESOURCE_ATTRIBUTES`, put that value in
`ENVIRONMENT_OVERRIDES`.

## 4. Request-weighted rollups

Averaging per-service averages gives a quiet service the same weight as a busy one; a worst-of-N
composite flips between services every datapoint. The executive tiles weight by requests, using
[Application Signals metric semantics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AppSignals-MetricsCollected.html):
`Fault` (Sum) counts 5xx and span errors, `Error` (Sum) counts 4xx, and `Latency` with the
`SampleCount` statistic is the request count. Hidden source rows feed the expression and only the
expression renders.

```text
Availability %  = (1 - sum(Fault) / sum(Latency SampleCount)) * 100          across services
Avg latency ms  = sum(Latency Avg_i * SampleCount_i) / sum(SampleCount_i)     across services
```

These are estate-wide service-request indicators, not an end-user SLI. Source the customer-impact
number from a front-door service, an Application Load Balancer, or a synthetic transaction.

## 5. Healthy reads zero, and zero is backed by an alarm

kube-state-metrics emits `*_reason` series only while the condition exists, so a healthy cluster
returns no series for `CrashLoopBackOff` and the widget renders blank. An operator cannot tell
blank-because-healthy from blank-because-the-pipeline-broke. Every counter is wrapped:

```promql
sum(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff",
  "@resource.k8s.cluster.name"="retail-store"}) or vector(0)
```

Classic metrics use `FILL(m,0)` on a hidden source row (DynamoDB publishes throttle metrics only
when throttling happens). Apply zero-fill only to counters and state conditions. Never zero-fill a
latency or utilization metric: a filled 0 ms asserts a measurement that did not happen.

Zero-fill has one gap: it returns 0 when the condition is absent and the same 0 when telemetry
has stopped. `alarms.sh` therefore creates an independent freshness alarm per plane (an
Application Signals heartbeat with missing data treated as breaching, and a PromQL
`absent_over_time(kube_node_info[15m])` alarm) plus a node-not-Ready alarm. The OTel heartbeat also
fires when Auto Mode removes the last worker node, which is the intended behavior for a freshness
signal.

## 6. Control-plane latency is a native histogram (version-specific)

With add-on v6.4.0-eksbuild.1, `apiserver_request_duration_seconds` is stored as a native
histogram: there are no `_bucket` series and no `le` label, so `histogram_quantile` runs on the
metric itself. `WATCH` and `CONNECT` are excluded so long-running verbs do not dominate the axis.

```promql
histogram_quantile(0.99, sum by (verb) (rate(
  apiserver_request_duration_seconds{verb!~"WATCH|CONNECT",
  "@resource.k8s.cluster.name"="retail-store"}[5m])))
```

If a future add-on version exposes classic buckets, the probe reports this query as FAILED or
EMPTY; switch to the `sum by (le, verb) (rate(..._bucket[5m]))` form.

## 7. Readable legends

A query grouped by two labels renders each series as a raw label map such as
`{node="i-0abc",condition="MemoryPressure"}`. `join_labels()` collapses them with `label_join`.
When several grouped queries share a widget, prefix a constant tag with `label_replace` first or
the legend cannot tell them apart. The special label value `__verbose__` forces the full label map
onto every series; the build counts it and the tests fail if it appears.

## 8. Dashboards as tested code

`PutDashboard` proves only that the JSON parsed. The builder records every PromQL query, metric
reference and log group in manifests; `probe.py` resolves each against live telemetry and reports
OK, EMPTY and FAILED per plane, together with the SHA-256 of the body it validated. Run it while
representative traffic is active so Application Signals metrics have data in the lookback window.

- **FAILED** is a blocking defect (bad query, bad dimension, missing log group).
- **EMPTY** is a workload condition to explain: no traffic, a resource Auto Mode scaled away, or a
  dimension that is valid but wrong (the namespace mistake in section 3 lands here). Never convert
  EMPTY to success.
- Dimension traps that surface as EMPTY: ALB host counts need both `LoadBalancer` and
  `TargetGroup`; `SelectLatency` exists only for MySQL while Aurora PostgreSQL publishes
  `CommitLatency`; metric math has no `COUNT()`, use `METRIC_COUNT` for `SEARCH` results.

Expect one "`x` property is not expected" warning per PromQL chart widget on `PutDashboard`. It is
cosmetic, and omitting `x` fails validation. `GetMetricWidgetImage` does not render PromQL chart
widgets, so open the deployed NOC in the console once before handing it to operators.

## 9. Amazon EKS Auto Mode makes valid widgets look blank

Auto Mode adds and removes nodes with demand and consolidates workloads as demand falls. When a
node, pod or container disappears, or an idle service produces no requests, a resource-scoped query
returns no series for the selected window and the widget renders blank rather than zero. Treat
blank as "no samples for this selector and time range", check the window and the resource, and
rely on the freshness alarms to separate expected scale-in from an observability failure.
