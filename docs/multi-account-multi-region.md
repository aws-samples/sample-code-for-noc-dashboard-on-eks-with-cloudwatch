# Running the NOC across accounts and Regions

The sample targets one cluster in one account and Region. Fleet operators have three options,
which differ in what a single PromQL expression can see.

## Option 1: one monitoring account per Region (CloudWatch cross-account observability)

[CloudWatch cross-account observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Unified-Cross-Account.html)
(Observability Access Manager, OAM) links source accounts to a monitoring account **within a
Region**. Metrics, logs, traces and Application Signals services and SLOs from every linked
account become visible in the monitoring account, so the Application Signals golden signals and the
AWS service metrics (ALB, DynamoDB, RDS) roll up cleanly on one dashboard there.

For a source-account query on a monitoring-account dashboard, set the account on the query:

- classic metric widgets: the `accountId` rendering property on each metric row
- PromQL chart widgets: the `accountId` field on each entry in `data.queries`

Each query still evaluates in exactly one account. To show the same cluster panel for three
source accounts, the builder emits three queries (or three widgets), each with its own
`accountId` and its own cluster selector. In this sample that is a loop over a
`CLUSTERS = [(account_id, cluster_name), ...]` list in `config.py`; ask your assistant to add it.

## Option 2: one dashboard, several Regions

The dashboard body carries a `region` on every widget, and PromQL chart widgets also accept a
`region` per query. A single NOC can therefore hold a Kubernetes panel per Region as long as
[PromQL and OTLP ingest are available in that Region](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL.html#CloudWatch-PromQL-Regions).
What you cannot do with this option is aggregate across Regions inside one PromQL expression:
the Kubernetes panels stay per Region, side by side, and the executive rollups stay per Region too.

The PromQL alarms in `alarms.sh` are Regional as well: create them in each Region and either point
them at a Regional SNS topic or forward to a central one.

## Option 3: one expression across the fleet (CloudWatch Metrics centralization)

[Cross-account cross-Region metrics centralization](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatchMetrics_Centralization.html)
copies metrics from source accounts and Regions in an AWS Organizations organization into a
destination account and Region. OTLP metrics are included and stay queryable with PromQL; the
copies gain `@aws.account` and `@aws.region` labels. That is what lets one expression span the
fleet:

```promql
sum by ("@aws.region") (kube_pod_status_phase{phase="Running"})
```

Trade-offs: only new data after the rule is created is centralized, the destination account's
metric quotas apply, and the first copy is free while a backup Region copy is charged. The
`@resource.k8s.cluster.name` selector still matters because several clusters now share one store.

## Which one

| You want | Use |
|---|---|
| One NOC per Region, many accounts | Option 1 (OAM) with per-query `accountId` |
| One NOC showing several Regions side by side | Option 2 (per-widget/per-query `region`) |
| Fleet-wide numbers in one expression or one alarm | Option 3 (Metrics centralization) |

Whatever you pick, keep the probe in the loop: it accepts a Region from `config.py`, and the
`accountId` on a query is a dimension mistake waiting to surface as EMPTY, exactly like the
namespace in the Environment value.
