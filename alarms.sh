#!/usr/bin/env bash
# alarms.sh: create the three alarms the NOC depends on. Values come from config.py.
#
# Zero-filled counters read 0 both when a condition is absent AND when telemetry has stopped, so
# each plane gets an independent freshness alarm, plus one alarm on node readiness.
#
#   ./alarms.sh              create the alarms (set SNS_TOPIC_ARN in config.py first)
#   DRY_RUN=1 ./alarms.sh    print the commands instead of running them
set -euo pipefail
cd "$(dirname "$0")"
eval "$(python3 config.py)"

run() { if [[ -n "${DRY_RUN:-}" ]]; then echo "[dry-run] $*"; else "$@"; fi; }

# PromQL alarm criteria are JSON with PromQL (and its quoted labels) inside a string. Build them
# with json.dumps rather than hand-escaping, and refuse to continue if the result does not parse.
criteria() {  # criteria <query> <pending-seconds> <recovery-seconds>
  python3 -c 'import json, sys
doc = {"PromQLCriteria": {"Query": sys.argv[1], "PendingPeriod": int(sys.argv[2]),
                          "RecoveryPeriod": int(sys.argv[3])}}
text = json.dumps(doc); json.loads(text); print(text)' "$1" "$2" "$3"
}

CL="\"@resource.k8s.cluster.name\"=\"${CLUSTER}\""

# 1. Application Signals heartbeat: the ui service must keep serving requests.
#    Missing data is treated as breaching, so a silent collector fires the alarm.
#    Environment is derived from config (eks:<cluster>/<namespace>), not hand-typed.
run aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "${CLUSTER}-appsignals-heartbeat" \
  --namespace ApplicationSignals --metric-name Latency --statistic SampleCount \
  --dimensions "Name=Environment,Value=${UI_ENVIRONMENT}" Name=Service,Value=ui \
  --period 300 --evaluation-periods 3 --datapoints-to-alarm 3 \
  --threshold 1 --comparison-operator LessThanThreshold \
  --treat-missing-data breaching \
  --alarm-actions "$SNS_TOPIC_ARN"

# 2. OTel (kube-state-metrics) heartbeat: kube_node_info exists for every node while collection
#    is healthy. absent_over_time returns a series only when nothing arrived for 15 minutes.
#    This also fires when Auto Mode removes the last worker node, which is the intended signal.
OTEL_CRITERIA=$(criteria "absent_over_time(kube_node_info{${CL}}[15m])" 0 300)
run aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "${CLUSTER}-otel-heartbeat" \
  --evaluation-criteria "$OTEL_CRITERIA" \
  --evaluation-interval 60 \
  --alarm-actions "$SNS_TOPIC_ARN"

# 3. Node not Ready for five minutes; recovers after five healthy minutes.
NODE_CRITERIA=$(criteria "(count(kube_node_info{${CL}}) - sum(kube_node_status_condition{condition=\"Ready\",status=\"true\",${CL}})) > 0" 300 300)
run aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "${CLUSTER}-node-not-ready" \
  --evaluation-criteria "$NODE_CRITERIA" \
  --evaluation-interval 60 \
  --alarm-actions "$SNS_TOPIC_ARN"

echo "Alarms: ${ALARM_NAMES}"
echo "PromQL alarms need a current AWS CLI (the --evaluation-criteria parameter). Add the AWS"
echo "recommended PromQL alarms for EKS as your baseline; these three are additions to it."
