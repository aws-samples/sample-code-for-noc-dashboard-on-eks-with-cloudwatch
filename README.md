# Building a single-pane NOC dashboard for Amazon EKS with CloudWatch

Companion code for the AWS Containers Blog post of the same name. It builds one Network Operations Center (NOC) overview and three
drilldowns for a cluster running the
[AWS retail store sample application](https://github.com/aws-containers/retail-store-sample-app),
validates every query against live telemetry, and deploys the result.

> **Sample-code notice:** This is sample code for non-production usage. Work with your security
> and legal teams to meet your organizational security, regulatory, and compliance requirements
> before deployment.

| Dashboard | Widgets | Window | Purpose |
|---|---|---|---|
| `<cluster>-noc` | 35 | 3 h | Single pane: request-weighted service rollups, incident counters, condensed sections |
| `<cluster>-eks-monitoring` | 9 | 3 h | Nodes, namespaces, control plane |
| `<cluster>-application-health` | 11 | 3 h | Golden signals per service, dependencies, JVM, ingress, backing services |
| `<cluster>-incident-response` | 12 | 1 h | Zero-filled failure counters, alarms, saturation, log search |

Kubernetes metrics come from [OTel Container Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/container-insights-eks-otel.html)
and are queried with [PromQL](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL.html);
service golden signals come from [Application Signals](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Application-Monitoring-Intro.html);
ingress and backing services come from their CloudWatch namespaces. The builder composes all
three into one view. Validated with the `amazon-cloudwatch-observability` add-on
**v6.4.0-eksbuild.1** on Amazon EKS Auto Mode, Kubernetes 1.34.

## Quick start (15 minutes)

You do not need to read the Python. Edit one file, run three commands.

```bash
git clone https://github.com/aws-samples/sample-code-for-noc-dashboard-on-eks-with-cloudwatch.git
cd sample-code-for-noc-dashboard-on-eks-with-cloudwatch
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# 1. Describe your environment (Region, account, cluster, service -> namespace map, SNS topic)
$EDITOR config.py

# 2. Confirm Application Signals sees the services exactly as config.py describes them
python3 probe.py --discover

# 3. Create the freshness alarms, then build, probe, deploy, and verify
./alarms.sh
./deploy.sh
```

Every script accepts `DRY_RUN=1` to print the AWS commands instead of running them.

`deploy.sh` stops if any query is `FAILED`, prints an explanation line for each `EMPTY`, and ends
with `DEPLOYED_MATCHES_BUILD=True` after a `GetDashboard` round trip. Then open the NOC in the
CloudWatch console once: PromQL chart widgets cannot be rendered through an API, so your eyes are
the last check.

Prerequisites: an Amazon EKS cluster with the add-on installed using `addon-config.json` (OTel
Container Insights and Application Signals on, Classic Container Insights off), a workload
instrumented for Application Signals, Python 3.12+, a current AWS CLI, and IAM permissions for
`cloudwatch:GetMetricData`, `cloudwatch:ListMetrics`, `cloudwatch:GetDashboard`,
`cloudwatch:PutDashboard`, `cloudwatch:PutMetricAlarm`, `cloudwatch:DeleteDashboards`,
`cloudwatch:DeleteAlarms`, `logs:DescribeLogGroups` and `application-signals:ListServices`.
Use a read-only role for the probe and a narrowly scoped role for deployment.

## Let your AI assistant do the customizing

This folder ships an agent skill (`.kiro/skills/eks-noc-dashboard/SKILL.md`, also summarized in
`AGENTS.md`) that teaches an assistant such as Kiro the file layout, the deploy order, and the
rules that keep the dashboards trustworthy. Open the folder in your assistant and ask in plain
language:

- "Set this NOC up in my account for cluster `payments-prod`; the services `api`, `ledger` and
  `notifier` run in namespace `payments`."
- "Add a widget for Amazon SQS queue depth for the `orders-events` queue to the application
  health drilldown."
- "The Availability gauge is blank. Why, and what do I check?"
- "Show the same infrastructure panels for our clusters in eu-west-1 and eu-central-1 on one NOC."
- "Move the SLO line from 99.9% to 99.5% and widen the incident window to two hours."

The assistant edits `config.py` or the builder, reruns the tests and the probe, and reports the
`BODY_SHA256` it validated. You review the result in the console.

## Structure

```
├── config.py                 # The only file with environment values; `python3 config.py` prints shell exports
├── noc_dashboard.py          # Builds the 4 dashboards + PromQL / metric / log manifests
├── probe.py                  # Validates manifests against live telemetry; --discover checks Application Signals
├── alarms.sh                 # Application Signals heartbeat, OTel heartbeat (PromQL), node not Ready
├── deploy.sh                 # build -> probe -> PutDashboard -> GetDashboard round trip
├── cleanup.sh                # Delete dashboards and alarms
├── addon-config.json         # CloudWatch Observability add-on configuration used for validation
├── requirements.txt          # boto3, botocore
├── tests/test_build.py       # Offline tests (no AWS credentials needed)
├── docs/
│   ├── patterns.md           # Why the queries look the way they do
│   └── multi-account-multi-region.md
├── AGENTS.md                 # Pointer for any AI coding assistant
├── LICENSE, CONTRIBUTING.md, CODE_OF_CONDUCT.md
└── .kiro/
    ├── skills/eks-noc-dashboard/SKILL.md   # Kiro skill: deploy order, rules, customization recipes
    └── steering/eks-noc-dashboard.md       # Always-on rules for Kiro sessions in this folder
```

Run the offline tests with `python3 -m unittest discover -s tests`.

## Three things that bit us (so they do not bite you)

1. **Application Signals `Environment` is `eks:<cluster>/<namespace>`**, the Kubernetes namespace,
   not the service name. The retail store sample uses same-named namespaces, so a probe run there
   cannot catch a builder that assumes they match. `config.SERVICE_NAMESPACES` makes the mapping
   explicit and `probe.py --discover` checks it against what CloudWatch has seen.
2. **A Logs Insights widget puts its log groups inside the query string** as `SOURCE '<group>'`.
   There is no `logGroupNames` field in the dashboard body (that name belongs to the CDK
   construct). The probe confirms the log group exists; only the console proves the widget draws.
3. **Metric names and histogram formats are add-on-version specific.** `apiserver_request_duration_seconds`
   is a native histogram with v6.4.0-eksbuild.1 (no `_bucket`, no `le`). Re-run the probe after
   every add-on upgrade; the version is printed with every report.

## Clean up

```bash
./cleanup.sh
```

Remove the add-on and the sample application too if you installed them only for this walkthrough.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
