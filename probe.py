"""probe.py: validate a dashboard's manifests against live telemetry before you deploy it.

    python probe.py <dashboard-name> [lookback-minutes]   validate one dashboard's manifests
    python probe.py --discover                            list the (Service, Environment) pairs
                                                          Application Signals has seen, and flag
                                                          any configured service whose derived
                                                          Environment is missing

Reads the manifests that noc_dashboard.py wrote and reports OK, EMPTY and FAILED separately for
each plane: PromQL queries (signed request to the CloudWatch PromQL endpoint), classic metric
references (GetMetricData) and Logs Insights widgets (the log groups they read must exist).
Prints the SHA-256 of the body the report belongs to and exits non-zero when anything FAILED.

A successful PutDashboard proves only that the JSON parsed. EMPTY is how dimension mistakes show
up: a wrong Application Signals Environment does not error, it returns no data.
"""
import datetime
import hashlib
import json
import pathlib
import sys
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from config import ADDON_VERSION_TESTED, PROMQL_ENDPOINT, REGION, SERVICES, env


def run_promql(query, credentials):
    """One PromQL instant query, signed with SigV4 for the CloudWatch endpoint."""
    body = urllib.parse.urlencode({"query": query})
    request = AWSRequest(method="POST", url=PROMQL_ENDPOINT, data=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    SigV4Auth(credentials, "monitoring", REGION).add_auth(request)
    http = urllib.request.Request(PROMQL_ENDPOINT, data=body.encode(), method="POST",
                                  headers=dict(request.headers))
    with urllib.request.urlopen(http, timeout=60) as response:
        return json.loads(response.read())["data"]["result"]


def probe_promql(entries, credentials):
    ok, empty, failed = 0, [], []
    for entry in entries:
        try:
            if run_promql(entry["query"], credentials):
                ok += 1
            else:
                empty.append(entry["id"])
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{entry['id']}: {exc}")
    return ok, empty, failed


def probe_classic(entries, lookback_minutes):
    """Resolve each unique metric reference through GetMetricData, 500 queries per call."""
    refs = list({(e["namespace"], e["metric"], tuple(sorted(e["dimensions"].items())),
                  e["stat"]): e for e in entries}.values())
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(minutes=lookback_minutes)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    ok, empty, failed = 0, [], []
    for offset in range(0, len(refs), 500):
        batch = refs[offset:offset + 500]
        queries = [{"Id": f"q{i}", "MetricStat": {"Period": 300, "Stat": ref["stat"], "Metric": {
                        "Namespace": ref["namespace"], "MetricName": ref["metric"],
                        "Dimensions": [{"Name": k, "Value": v} for k, v in ref["dimensions"].items()]}}}
                   for i, ref in enumerate(batch)]
        results = cloudwatch.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end)
        by_id = {r["Id"]: r for r in results["MetricDataResults"]}
        for i, ref in enumerate(batch):
            result = by_id.get(f"q{i}")
            label = f"{ref['namespace']}/{ref['metric']} {ref['dimensions']}"
            if result is None or result["StatusCode"] != "Complete":
                failed.append(label)
            elif result["Values"]:
                ok += 1
            else:
                empty.append(label)
    return len(refs), ok, empty, failed


def probe_logs(entries):
    """Confirm every log group a Logs Insights widget reads exists in this Region.

    The widget itself cannot be rendered through an API (GetMetricWidgetImage does not cover log
    widgets), so a missing log group is the one defect that can be caught before opening the
    console. The `SOURCE '<group>'` form inside the query string is the documented body schema.
    """
    logs = boto3.client("logs", region_name=REGION)
    groups = sorted({g for e in entries for g in e["log_groups"]})
    ok, failed = 0, []
    for group in groups:
        found = logs.describe_log_groups(logGroupNamePrefix=group, limit=50)["logGroups"]
        if any(g["logGroupName"] == group for g in found):
            ok += 1
        else:
            failed.append(f"log group not found: {group}")
    return len(groups), ok, failed


def discover():
    """List the services Application Signals discovered and compare with config.py."""
    signals = boto3.client("application-signals", region_name=REGION)
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(hours=24)
    seen, token = {}, None
    while True:
        kwargs = {"StartTime": start, "EndTime": end, "MaxResults": 100}
        if token:
            kwargs["NextToken"] = token
        page = signals.list_services(**kwargs)
        for summary in page["ServiceSummaries"]:
            keys = summary["KeyAttributes"]
            if keys.get("Type") == "Service":
                seen[(keys.get("Name"), keys.get("Environment"))] = True
        token = page.get("NextToken")
        if not token:
            break
    print(f"Application Signals services seen in the last 24h ({REGION}):")
    for name, environment in sorted(seen):
        print(f"  Service={name}  Environment={environment}")
    missing = [s for s in SERVICES if (s, env(s)) not in seen]
    for s in missing:
        print(f"MISSING {s}: config derives Environment={env(s)} but Application Signals has not "
              "reported that pair. Check SERVICE_NAMESPACES (the namespace, not the service name) "
              "or ENVIRONMENT_OVERRIDES in config.py.")
    print(f"CONFIG_MATCHES={len(SERVICES) - len(missing)}/{len(SERVICES)}")
    return 1 if missing else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--discover":
        sys.exit(discover())
    name = sys.argv[1]
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    promql = json.loads(pathlib.Path(f"{name}_promql.json").read_text())
    classic = json.loads(pathlib.Path(f"{name}_classic.json").read_text())
    logs_manifest = pathlib.Path(f"{name}_logs.json")
    logs = json.loads(logs_manifest.read_text()) if logs_manifest.exists() else []
    p_ok, p_empty, p_failed = probe_promql(promql, credentials)
    c_total, c_ok, c_empty, c_failed = probe_classic(classic, lookback)
    l_total, l_ok, l_failed = probe_logs(logs)
    print(f"PROMQL  TOTAL={len(promql)} OK={p_ok} EMPTY={len(p_empty)} FAILED={len(p_failed)}")
    print(f"CLASSIC UNIQUE={c_total} OK={c_ok} EMPTY={len(c_empty)} FAILED={len(c_failed)}")
    print(f"LOGS    GROUPS={l_total} OK={l_ok} FAILED={len(l_failed)}")
    for status, items in (("EMPTY ", p_empty + c_empty), ("FAILED", p_failed + c_failed + l_failed)):
        for item in items:
            print(f"  {status} {item}")
    body = pathlib.Path(f"{name}_body.json").read_bytes()
    print(f"BODY_SHA256={hashlib.sha256(body).hexdigest()}")
    print(f"ADDON_VERSION_TESTED={ADDON_VERSION_TESTED}")
    sys.exit(1 if p_failed or c_failed or l_failed else 0)


if __name__ == "__main__":
    main()
