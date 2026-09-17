"""noc_dashboard.py: build the NOC and its three drilldowns as code.

Run `python noc_dashboard.py` to write, for the NOC and each drilldown:
  <name>_body.json    the dashboard body for PutDashboard
  <name>_promql.json  every PromQL query on the dashboard (validated by probe.py)
  <name>_classic.json every CloudWatch metric reference on the dashboard (validated by probe.py)
  <name>_logs.json    every Logs Insights widget and the log groups it reads (validated by probe.py)

All environment values come from config.py. See docs/patterns.md for why the queries look the
way they do (cluster scoping, zero-fill, request weighting, label joins).
"""
import json
import pathlib

from config import (ACCOUNT, ALARM_ARNS, ALB, APP_LOG_GROUP, CLUSTER, CONSOLE, DDB_TABLE,
                    EKS_VERSION, JVM_SERVICES, REGION, SERVICES, TARGET_GROUP, env)

# ---------------------------------------------------------------- helpers
# CL scopes every PromQL query to one cluster; a second cluster in the account must not leak in.
CL = f'"@resource.k8s.cluster.name"="{CLUSTER}"'
NODE_BY = '"@resource.k8s.node.name"'
APP_SIGNALS = "ApplicationSignals"
RED, ORANGE, GREEN = "#d13212", "#ff9900", "#2ca02c"


def zero_filled(expr):
    """Counters read 0 when healthy instead of rendering blank."""
    return f"{expr} or vector(0)"


def join_labels(expr, *labels, sep=" ", dst="series"):
    """Collapse a multi-label result into one readable legend entry."""
    src = ", ".join(f'"{name}"' for name in labels)
    return f'sum by ({dst}) (label_join({expr}, "{dst}", "{sep}", {src}))'


def line_style(width=2, filled=False, stacked=False):
    """lineOptions must carry every field or schema validation fails."""
    return {"lineOptions": {"width": width, "pattern": "solid", "spline": False,
                            "filled": filled, "stacked": stacked}}


def pct_axis(title="%"):
    return [{"min": 0, "max": 100, "title": title}]


def h_annotation(value, label=None, color=ORANGE):
    return {"horizontal": [{"type": "static", "label": label or str(value),
                            "value": value, "color": color}]}


# ---------------------------------------------------------------- canvas
class Canvas:
    """Accumulate widgets with automatic row layout on the 24-column grid.

    Records every PromQL query, classic metric reference and log widget in manifests so that
    probe.py can validate the dashboard against live telemetry before it is deployed.
    """
    GRID = 24
    BODY_BUDGET_BYTES = 100_000  # project guardrail, not an AWS quota

    def __init__(self, start="-PT3H"):
        self.widgets, self.promql, self.classic, self.logs = [], [], [], []
        self.start = start
        self._x = self._y = self._row_h = 0

    def newline(self):
        if self._x:
            self._y += self._row_h
            self._x = self._row_h = 0
        return self

    def _place(self, width, height):
        if self._x + width > self.GRID:
            self.newline()
        x, y = self._x, self._y
        self._x += width
        self._row_h = max(self._row_h, height)
        return x, y

    def text(self, markdown, height=1, width=GRID):
        self.newline()
        x, y = self._place(width, height)
        self.widgets.append({"type": "text", "x": x, "y": y, "width": width,
                             "height": height, "properties": {"markdown": markdown}})
        return self.newline()

    def chart(self, title, queries, width=8, height=6, view="line", style=None,
              legend="bottom", y_axis=None, annotations=None):
        """PromQL chart widget from (id, query, label) tuples."""
        x, y = self._place(width, height)
        rendered = []
        for qid, expr, label in queries:
            entry = {"id": qid, "type": "cloudwatch-metrics",
                     "language": "PromQL", "query": expr}
            if label:
                entry["label"] = label
            rendered.append(entry)
            self.promql.append({"widget": title, "id": qid, "query": expr})
        plot = {"style": style if style is not None else line_style()}
        if legend:
            plot["legend"] = {"position": legend, "show": True}
        if y_axis:
            plot["yAxis"] = y_axis
        if annotations:
            plot["annotations"] = annotations
        self.widgets.append({"type": "chart", "x": x, "y": y, "width": width,
                             "height": height,
                             "properties": {"view": view, "title": title, "region": REGION,
                                            "data": {"queries": rendered},
                                            "plotOptions": plot}})
        return self

    def number(self, title, qid, query, label=None, width=6, height=5):
        """Single-value PromQL widget."""
        return self.chart(title, [(qid, query, label)], width=width, height=height,
                          view="number", legend=None,
                          style={"numberOptions": {"sparkline": True, "truncate": True}})

    @staticmethod
    def _classic_y_axis(y_axis):
        """Classic widgets use {"left": {...}} and "label", not "title"."""
        axis = dict(y_axis[0])
        if "title" in axis:
            axis["label"] = axis.pop("title")
        return {"left": axis}

    @staticmethod
    def _classic_annotations(annotations):
        """Classic widgets reject the chart widget's "type": "static"."""
        return {edge: [{k: v for k, v in e.items() if k != "type"} for e in entries]
                for edge, entries in annotations.items()}

    def metric(self, title, metrics, width=12, height=6, view="timeSeries",
               stat="Average", period=300, y_axis=None, annotations=None,
               stacked=False, sparkline=False):
        """Classic metric widget; records literal metric rows for validation."""
        x, y = self._place(width, height)
        props = {"title": title, "view": view, "region": REGION, "stat": stat,
                 "period": period, "metrics": metrics}
        if view == "timeSeries":
            props["stacked"] = stacked
        if view == "singleValue":
            props["sparkline"] = sparkline
        if y_axis:
            props["yAxis"] = self._classic_y_axis(y_axis)
        if annotations:
            props["annotations"] = self._classic_annotations(annotations)
        self.widgets.append({"type": "metric", "x": x, "y": y, "width": width,
                             "height": height, "properties": props})
        for entry in metrics:
            if not entry or isinstance(entry[0], dict):
                continue                                   # metric math row
            opts = entry[-1] if isinstance(entry[-1], dict) else {}
            raw = entry[:-1] if opts else entry
            dims = {raw[i]: raw[i + 1] for i in range(2, len(raw) - 1, 2)}
            self.classic.append({"widget": title, "namespace": raw[0], "metric": raw[1],
                                 "dimensions": dims, "stat": opts.get("stat", stat)})
        return self

    def alarms(self, title, alarm_arns, width=12, height=6):
        x, y = self._place(width, height)
        self.widgets.append({"type": "alarm", "x": x, "y": y, "width": width,
                             "height": height,
                             "properties": {"title": title, "alarms": alarm_arns,
                                            "sortBy": "stateUpdatedTimestamp",
                                            "states": ["ALARM", "INSUFFICIENT_DATA", "OK"]}})
        return self

    def log(self, title, log_groups, query_lines, width=GRID, height=6):
        """Logs Insights widget. The dashboard body schema puts the log groups inside the query
        string as `SOURCE '<group>'` entries (there is no logGroupNames field in the body; that
        name belongs to the CDK construct). The groups are recorded so probe.py can confirm
        they exist."""
        x, y = self._place(width, height)
        sources = " | ".join(f"SOURCE '{g}'" for g in log_groups)
        query = sources + "\n| " + "\n| ".join(query_lines)
        self.widgets.append({"type": "log", "x": x, "y": y, "width": width,
                             "height": height,
                             "properties": {"title": title, "region": REGION,
                                            "query": query, "view": "table"}})
        self.logs.append({"widget": title, "log_groups": list(log_groups)})
        return self

    def build(self):
        body = json.dumps({"start": self.start, "periodOverride": "inherit",
                           "widgets": self.widgets}, separators=(",", ":"))
        if len(body) > self.BODY_BUDGET_BYTES:
            raise ValueError(f"dashboard body is {len(body):,} bytes; "
                             f"budget is {self.BODY_BUDGET_BYTES:,}")
        return body

    def write(self, name, base=pathlib.Path(".")):
        body = self.build()
        (base / f"{name}_body.json").write_text(body)
        (base / f"{name}_promql.json").write_text(json.dumps(self.promql, indent=2))
        (base / f"{name}_classic.json").write_text(json.dumps(self.classic, indent=2))
        (base / f"{name}_logs.json").write_text(json.dumps(self.logs, indent=2))
        verbose = sum(1 for w in self.widgets if w["type"] == "chart"
                      for q in w["properties"]["data"]["queries"]
                      if q.get("label") == "__verbose__")
        print(f"{name}: widgets={len(self.widgets)} promql={len(self.promql)} "
              f"classic={len(self.classic)} logs={len(self.logs)} bytes={len(body)} "
              f"verbose_labels={verbose}")


# ---------------------------------------------------------------- executive summary
def _sig(metric, services=None, stat=None, visible=True, prefix=""):
    """Application Signals metric rows for a set of services.

    The Environment dimension comes from config.env(): eks:<cluster>/<namespace>.
    """
    rows = []
    for s in services or SERVICES:
        opts = {"label": s}
        if stat:
            opts["stat"] = stat
        if prefix:
            opts["id"] = f"{prefix}{s}"
        if not visible:
            opts["visible"] = False
        rows.append([APP_SIGNALS, metric, "Environment", env(s), "Service", s, opts])
    return rows


def exec_summary(c):
    """Request-weighted service rollups and infrastructure headline tiles."""
    c.text("\n".join([
        f"# NOC: {CLUSTER}",
        f"Cluster **{CLUSTER}** · Account **{ACCOUNT}** · Region **{REGION}** "
        f"· EKS **{EKS_VERSION}** Auto Mode",
        "",
        "Executive summary. Sections below drill down into **Infrastructure**, "
        "**Application Health**, and **Incident Response**.",
        "",
        f"Availability and latency are **request-weighted across all {len(SERVICES)} "
        "services** (not a worst-case service, and not an unweighted mean). "
        "Availability = `1 - faults/requests`. Per-service breakdowns are in the "
        "Application health section below.",
    ]), height=4)

    c.text("## Executive summary: service level")
    avail = (_sig("Fault", stat="Sum", visible=False, prefix="xf_") +
             _sig("Latency", stat="SampleCount", visible=False, prefix="xn_"))
    f_sum = "+".join(f"xf_{s}" for s in SERVICES)
    n_sum = "+".join(f"xn_{s}" for s in SERVICES)
    avail.append([{"expression": f"(1-({f_sum})/({n_sum}))*100",
                   "label": "Availability %", "id": "x_avail"}])
    c.metric("Availability % (all services, request-weighted)", avail, width=6,
             height=5, view="gauge", period=300, y_axis=[{"min": 0, "max": 100}])

    reqs = _sig("Latency", stat="SampleCount", visible=False, prefix="xr_")
    reqs.append([{"expression": "SUM([" + ",".join(f"xr_{s}" for s in SERVICES) + "])",
                  "label": "Requests/min", "id": "x_req"}])
    c.metric("Request rate (req/min)", reqs, width=6, height=5, view="singleValue",
             sparkline=True, period=60)

    lat = (_sig("Latency", stat="Average", visible=False, prefix="xl_") +
           _sig("Latency", stat="SampleCount", visible=False, prefix="xc_"))
    num = "+".join(f"xl_{s}*xc_{s}" for s in SERVICES)
    den = "+".join(f"xc_{s}" for s in SERVICES)
    lat.append([{"expression": f"({num})/({den})", "label": "Avg latency (ms)",
                 "id": "x_lat"}])
    c.metric("Avg latency (ms), request-weighted", lat, width=6, height=5,
             view="singleValue", sparkline=True, period=300)

    fails = (_sig("Fault", stat="Sum", visible=False, prefix="xfc_") +
             _sig("Error", stat="Sum", visible=False, prefix="xec_"))
    fails.append([{"expression": "+".join(f"xfc_{s}" for s in SERVICES),
                   "label": "Faults", "id": "x_faults"}])
    fails.append([{"expression": "+".join(f"xec_{s}" for s in SERVICES),
                   "label": "Errors", "id": "x_errors"}])
    c.metric("Failed requests (5 min)", fails, width=6, height=5, view="singleValue",
             sparkline=True, period=300)

    c.text("## Executive summary: infrastructure")
    c.number("Nodes Ready", "x_nodes_ready",
             f'sum(kube_node_status_condition{{condition="Ready",status="true",{CL}}})',
             "Ready", width=4, height=5)
    c.number("Namespaces", "x_ns",
             f'count(kube_namespace_status_phase{{phase="Active",{CL}}} == 1)',
             "Active", width=4, height=5)
    c.number("Pods running", "x_pods",
             f'sum(kube_pod_status_phase{{phase="Running",{CL}}})', "Running",
             width=4, height=5)
    c.number("Services", "x_svc", f"count(kube_service_info{{{CL}}})", "Services",
             width=4, height=5)
    c.number("Pod headroom", "x_headroom", zero_filled(
        f'sum(kube_node_status_allocatable{{resource="pods",{CL}}}) - '
        f'sum(kube_pod_status_phase{{phase="Running",{CL}}})'),
        "Free slots", width=4, height=5)
    c.number("Unavailable replicas", "x_unavail", zero_filled(
        f"sum(kube_deployment_status_replicas_unavailable{{{CL}}})"),
        "Unavailable", width=4, height=5)


def navigation(c):
    """Text widget with links to the three drilldowns."""
    nav = ["## Drill-down dashboards", ""]
    for label, name, blurb in [
        ("Infrastructure", f"{CLUSTER}-eks-monitoring",
         "nodes, capacity, control plane, namespace detail"),
        ("Application Health", f"{CLUSTER}-application-health",
         "per-service golden signals, dependencies, JVM, ingress, backing services"),
        ("Incident Response", f"{CLUSTER}-incident-response",
         "failure counters, saturation, control-plane degradation, log search"),
    ]:
        nav.append(f"- **[{label}]({CONSOLE}{name})**: {blurb}")
    nav += ["", "_Reading order: executive numbers -> incident counters (is anything "
            "broken) -> application golden signals (is the customer affected) -> "
            "infrastructure (is the infrastructure healthy)._"]
    c.text("\n".join(nav), height=4)


# ---------------------------------------------------------------- sections
# Each section emits its condensed widgets first and returns when detail is False, so the NOC
# and its drilldown can never disagree about a counter's query.
def incident_response(c, detail=True):
    """Six zero-filled failure counters; detail adds alarms, saturation and logs."""
    c.text("## Incident response: triage signals\n"
           "Counters read **0** when healthy (zero-filled), so a blank widget "
           "means *no data* rather than *no problem*.")
    c.number("Nodes NOT Ready", "ir_nodes_bad", zero_filled(
        f"count(kube_node_info{{{CL}}}) - "
        f'sum(kube_node_status_condition{{condition="Ready",status="true",{CL}}})'),
        "Not Ready", width=4, height=5)
    c.number("CrashLoopBackOff", "ir_crashloop", zero_filled(
        f'sum(kube_pod_container_status_waiting_reason{{reason="CrashLoopBackOff",{CL}}})'),
        "Containers", width=4, height=5)
    c.number("Image pull failures", "ir_imagepull", zero_filled(
        "sum(kube_pod_container_status_waiting_reason"
        f'{{reason=~"ImagePullBackOff|ErrImagePull",{CL}}})'), "Containers", width=4, height=5)
    c.number("OOMKilled containers", "ir_oomkilled", zero_filled(
        f'sum(kube_pod_container_status_terminated_reason{{reason="OOMKilled",{CL}}})'),
        "Containers", width=4, height=5)
    c.number("Failed pods", "ir_failed", zero_filled(
        f'sum(kube_pod_status_phase{{phase="Failed",{CL}}})'), "Pods", width=4, height=5)
    c.number("Pods not scheduled", "ir_unsched", zero_filled(
        f'sum(kube_pod_status_scheduled{{condition="false",{CL}}})'), "Pods",
        width=4, height=5)
    if not detail:
        return                                             # the NOC stops here

    # Drilldown: alarm state, saturation, control plane degradation, log search.
    c.alarms("CloudWatch alarms", ALARM_ARNS, width=24, height=6)
    c.chart("Node pressure conditions", [
        ("ir_pressure", join_labels(
            "sum by (node, condition) (kube_node_status_condition"
            f'{{condition=~"MemoryPressure|DiskPressure|PIDPressure",status="true",{CL}}})',
            "condition", "node"), None),
    ], width=8, height=6)
    c.chart("Pod headroom (allocatable vs running)", [
        ("ir_alloc", f'sum(kube_node_status_allocatable{{resource="pods",{CL}}})',
         "Allocatable pods"),
        ("ir_running", f'sum(kube_pod_status_phase{{phase="Running",{CL}}})', "Running pods"),
    ], width=8, height=6)
    c.chart("API server errors and terminations", [
        ("ir_api_5xx", zero_filled(
            f'sum(rate(apiserver_request_total{{code=~"5..",{CL}}}[5m]))'), "5xx rate"),
        ("ir_api_term", zero_filled(
            f"sum(rate(apiserver_request_terminations_total{{{CL}}}[5m]))"),
         "Terminated requests"),
    ], width=8, height=6)
    c.log("Application errors (last 50)", [APP_LOG_GROUP], [
        "fields @timestamp, kubernetes.namespace_name, kubernetes.container_name, log",
        "filter log like /(?i)(error|exception|fatal|panic|timeout|refused)/",
        "sort @timestamp desc",
        "limit 50",
    ], height=8)


def app_health(c, detail=True):
    """Golden signals per service; detail adds dependencies, JVM, ingress, backing services."""
    c.text("## Application health: service golden signals "
           f"(Application Signals: {' · '.join(SERVICES)})")
    avail = _sig("Fault", stat="Average", visible=False, prefix="avf_")
    for s in SERVICES:
        avail.append([{"expression": f"(1-avf_{s})*100", "label": s, "id": f"av_{s}"}])
    c.metric("Availability % by service", avail, width=12, height=6, period=300,
             y_axis=[{"min": 90, "max": 100.5}],
             annotations=h_annotation(99.9, "99.9% SLO", RED))
    c.metric("Latency by service: average (ms)", _sig("Latency", stat="Average"),
             width=6, height=6, period=300)
    c.metric("Latency by service: p99 (ms)", _sig("Latency", stat="p99"),
             width=6, height=6, period=300)
    c.metric("Fault rate % by service",
             _sig("Fault", stat="Average", visible=False, prefix="ff_") +
             [[{"expression": f"ff_{s}*100", "label": s, "id": f"fr_{s}"}] for s in SERVICES],
             width=8, height=6, period=300, y_axis=[{"min": 0}])
    c.metric("Error rate % by service",
             _sig("Error", stat="Average", visible=False, prefix="ee_") +
             [[{"expression": f"ee_{s}*100", "label": s, "id": f"er_{s}"}] for s in SERVICES],
             width=8, height=6, period=300, y_axis=[{"min": 0}])
    c.metric("Request volume by service (req/min)", _sig("Latency", stat="SampleCount"),
             width=8, height=6, period=60, stacked=True)
    if not detail:
        return                                             # the NOC stops here

    # Drilldown: dependencies, JVM runtime, ingress and backing services.
    c.metric("Dependency latency by remote service (ms)", [
        [APP_SIGNALS, "Latency", "RemoteService", "AWS::DynamoDB", {"label": "DynamoDB"}],
        [APP_SIGNALS, "Latency", "RemoteService", "postgresql", {"label": "PostgreSQL"}],
    ], width=12, height=6, period=300)
    if JVM_SERVICES:
        c.metric("JVM heap used (bytes)", _sig("JVMMemoryHeapUsed", JVM_SERVICES),
                 width=12, height=6, period=300)
    if ALB and TARGET_GROUP:
        c.metric("ALB target health (ui target group)", [
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", ALB, "TargetGroup",
             TARGET_GROUP, {"label": "Healthy hosts", "stat": "Minimum", "color": GREEN}],
            ["AWS/ApplicationELB", "UnHealthyHostCount", "LoadBalancer", ALB, "TargetGroup",
             TARGET_GROUP, {"label": "Unhealthy hosts", "stat": "Maximum", "color": RED}],
        ], width=12, height=6, period=300)
    if DDB_TABLE:
        c.metric("DynamoDB throttling (carts)", [
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", DDB_TABLE,
             {"id": "dt1", "stat": "Sum", "visible": False}],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", DDB_TABLE,
             {"id": "dt2", "stat": "Sum", "visible": False}],
            [{"expression": "FILL(dt1,0)", "label": "Read throttle events", "id": "df1"}],
            [{"expression": "FILL(dt2,0)", "label": "Write throttle events", "id": "df2"}],
        ], width=12, height=6, stat="Sum", period=300, y_axis=[{"min": 0}])


def infrastructure(c, detail=True):
    """Node, namespace and control plane health; detail adds filesystem, latency and etcd."""
    c.text("## Infrastructure: nodes, namespaces, control plane")
    c.chart("Node Ready condition (1 = Ready)", [
        ("in_ready", "sum by (node) "
         f'(kube_node_status_condition{{condition="Ready",status="true",{CL}}})', None),
    ], width=8, height=6, y_axis=[{"min": 0, "max": 1.2}])
    c.chart("Node CPU utilization % by node", [
        ("in_cpu", f'avg by ({NODE_BY}) ({{"k8s.node.cpu.utilization",{CL}}}) * 100', None),
    ], width=8, height=6, y_axis=pct_axis(), annotations=h_annotation(80))
    c.chart("Node memory utilization % by node", [
        ("in_mem",
         f'100 * sum by ({NODE_BY}) ({{"k8s.node.memory.working_set",{CL}}}) / '
         f'(sum by ({NODE_BY}) ({{"k8s.node.memory.working_set",{CL}}}) + '
         f'sum by ({NODE_BY}) ({{"k8s.node.memory.available",{CL}}}))', None),
    ], width=8, height=6, y_axis=pct_axis(), annotations=h_annotation(80))
    c.chart("Running pods per namespace", [
        ("in_ns_pods", f'sum by (namespace) (kube_pod_status_phase{{phase="Running",{CL}}})',
         None),
    ], width=12, height=6, legend="right", style=line_style(width=1, filled=True, stacked=True))
    c.chart("API server request and error rate", [
        ("in_api_all", f"sum(rate(apiserver_request_total{{{CL}}}[5m]))", "All requests"),
        ("in_api_5xx", zero_filled(
            f'sum(rate(apiserver_request_total{{code=~"5..",{CL}}}[5m]))'), "5xx"),
        ("in_api_4xx", zero_filled(
            f'sum(rate(apiserver_request_total{{code=~"4..",{CL}}}[5m]))'), "4xx"),
    ], width=12, height=6)
    if not detail:
        return                                             # the NOC stops here

    # Drilldown: node capacity and control plane latency.
    c.chart("Node filesystem utilization % by node", [
        ("nd_fs", f'100 * sum by ({NODE_BY}) ({{"k8s.node.filesystem.usage",{CL}}}) / '
         f'sum by ({NODE_BY}) ({{"k8s.node.filesystem.capacity",{CL}}})', None),
    ], width=8, height=6, y_axis=pct_axis(), annotations=h_annotation(85, "85%", RED))
    # apiserver_request_duration_seconds is stored as a native histogram with add-on
    # v6.4.0-eksbuild.1 (no _bucket series, no le label), so histogram_quantile runs on the
    # metric itself. Re-run probe.py after an add-on upgrade; if this query returns FAILED,
    # switch to the classic sum by (le) (rate(..._bucket[5m])) form.
    c.chart("API server p99 latency by verb (s)", [
        ("cp_p99", "histogram_quantile(0.99, sum by (verb) (rate("
         f'apiserver_request_duration_seconds{{verb!~"WATCH|CONNECT",{CL}}}[5m])))', None),
    ], width=8, height=6, legend="right")
    c.chart("etcd request errors", [
        ("cp_etcd_err", zero_filled(f"sum(rate(etcd_request_errors_total{{{CL}}}[5m]))"),
         "etcd errors"),
    ], width=8, height=6)


# ---------------------------------------------------------------- compose
def build_all(base=pathlib.Path(".")):
    """Compose the NOC from condensed sections and the drilldowns from detail."""
    noc = Canvas(start="-PT3H")
    exec_summary(noc)
    navigation(noc)
    incident_response(noc, detail=False)
    noc.alarms("CloudWatch alarm state", ALARM_ARNS, width=24, height=6)
    app_health(noc, detail=False)
    infrastructure(noc, detail=False)
    noc.write(f"{CLUSTER}-noc", base)

    infra = Canvas(start="-PT3H")
    infrastructure(infra, detail=True)
    infra.write(f"{CLUSTER}-eks-monitoring", base)

    app = Canvas(start="-PT3H")
    app_health(app, detail=True)
    app.write(f"{CLUSTER}-application-health", base)

    incident = Canvas(start="-PT1H")                       # triage cares about now
    incident_response(incident, detail=True)
    incident.write(f"{CLUSTER}-incident-response", base)


if __name__ == "__main__":
    build_all()
