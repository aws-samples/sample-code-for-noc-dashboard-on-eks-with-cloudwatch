#!/usr/bin/env bash
# cleanup.sh: delete the four dashboards and the three alarms created by this sample.
#
#   ./cleanup.sh             delete
#   DRY_RUN=1 ./cleanup.sh   print the commands instead of running them
set -euo pipefail
cd "$(dirname "$0")"
eval "$(python3 config.py)"

run() { if [[ -n "${DRY_RUN:-}" ]]; then echo "[dry-run] $*"; else "$@"; fi; }

# shellcheck disable=SC2086
run aws cloudwatch delete-dashboards --region "$REGION" --dashboard-names $DASHBOARDS
# shellcheck disable=SC2086
run aws cloudwatch delete-alarms --region "$REGION" --alarm-names $ALARM_NAMES
run rm -f ./*_body.json ./*_promql.json ./*_classic.json ./*_logs.json ./*_put.json deployed_noc.json
echo "Deleted dashboards (${DASHBOARDS}) and alarms (${ALARM_NAMES})."
echo "If you installed the CloudWatch Observability add-on or the sample application only for this"
echo "walkthrough, remove them too to stop ingestion charges."
