#!/usr/bin/env bash
# deploy.sh: build the four dashboard bodies, validate them against live telemetry, deploy them,
# and prove the deployed NOC matches the build. Stops at the first FAILED probe.
#
#   ./deploy.sh              build + probe + deploy + verify
#   ./deploy.sh --no-probe   skip the live probe (only for accounts where you cannot query yet)
#   DRY_RUN=1 ./deploy.sh    build only, print the AWS commands instead of running them
set -euo pipefail
cd "$(dirname "$0")"
eval "$(python3 config.py)"

run() { if [[ -n "${DRY_RUN:-}" ]]; then echo "[dry-run] $*"; else "$@"; fi; }

echo "== build"
python3 noc_dashboard.py

if [[ "${1:-}" != "--no-probe" && -z "${DRY_RUN:-}" ]]; then
  echo "== probe (run while representative traffic is active)"
  python3 probe.py --discover || { echo "Fix SERVICE_NAMESPACES in config.py before deploying."; exit 1; }
  for name in $DASHBOARDS; do
    echo "-- $name"
    python3 probe.py "$name" 180
  done
fi

echo "== deploy"
for name in $DASHBOARDS; do
  if [[ -n "${DRY_RUN:-}" ]]; then
    run aws cloudwatch put-dashboard --region "$REGION" --dashboard-name "$name" \
      --dashboard-body "file://${name}_body.json"
    continue
  fi
  aws cloudwatch put-dashboard --region "$REGION" --dashboard-name "$name" \
    --dashboard-body "file://${name}_body.json" --output json > "${name}_put.json"
  # One "x property is not expected" warning per PromQL chart widget is cosmetic and expected
  # (omitting x fails validation). Messages containing "Should have", "Should be", "Should match"
  # or "not found" are real errors and stop the deploy.
  python3 - "$name" <<'PY'
import json, sys
name = sys.argv[1]
messages = json.load(open(f"{name}_put.json")).get("DashboardValidationMessages", [])
real = [m for m in messages if any(k in m.get("Message", "")
        for k in ("Should have", "Should be", "Should match", "not found"))]
cosmetic = len(messages) - len(real)
print(f"{name}: validation messages={len(messages)} cosmetic={cosmetic} real={len(real)}")
for m in real:
    print(f"  REAL {m.get('DataPath', '')}: {m.get('Message', '')}")
raise SystemExit(1 if real else 0)
PY
done

if [[ -n "${DRY_RUN:-}" ]]; then
  echo "[dry-run] skipping GetDashboard verification"
  exit 0
fi

echo "== verify: deployed NOC equals the build"
aws cloudwatch get-dashboard --region "$REGION" --dashboard-name "${CLUSTER}-noc" \
  --query DashboardBody --output text > deployed_noc.json
python3 - "$CLUSTER" <<'PY'
import json, sys
cluster = sys.argv[1]
same = json.load(open("deployed_noc.json")) == json.load(open(f"{cluster}-noc_body.json"))
print("DEPLOYED_MATCHES_BUILD=" + str(same))
raise SystemExit(0 if same else 1)
PY

echo "Open the NOC and look at it once: GetMetricWidgetImage does not render PromQL chart widgets,"
echo "so the console is the only place that proves every widget draws."
echo "https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#dashboards/dashboard/${CLUSTER}-noc"
