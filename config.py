"""config.py: the single place to describe your environment.

Every other file in this folder (the builder, the probe, the alarm and deploy scripts, the tests)
imports its settings from here, so targeting a new cluster is a configuration change and never a
code change. Run `python config.py` to print the same values as shell exports for the .sh scripts.

Validated against the amazon-cloudwatch-observability add-on v6.4.0-eksbuild.1 on Amazon EKS
Auto Mode (Kubernetes 1.34). Metric names (k8s.*, kube_*, apiserver_*) and the histogram
representation of apiserver_request_duration_seconds can change between add-on versions;
re-run probe.py after every add-on upgrade.
"""

# ------------------------------------------------------------------ Your environment
REGION = "us-east-1"
ACCOUNT = "111122223333"
CLUSTER = "retail-store"
EKS_VERSION = "1.34"

# Application Signals identifies a service by (Service, Environment). On Amazon EKS the default
# Environment is "eks:<cluster-name>/<namespace>", where <namespace> is the KUBERNETES NAMESPACE
# the pods run in, NOT the service name. The two only coincide when you deploy each service into
# a namespace of the same name (the retail store sample does). Map every service to its namespace
# here; a wrong namespace does not error, it makes every Application Signals widget render EMPTY.
# Run `python probe.py --discover` to list the (Service, Environment) pairs CloudWatch has seen.
SERVICE_NAMESPACES = {
    "ui": "ui",
    "carts": "carts",
    "checkout": "checkout",
    "orders": "orders",
}

# If a service overrides deployment.environment (OTEL_RESOURCE_ATTRIBUTES), put the full value
# here and it wins over the namespace-derived default.
ENVIRONMENT_OVERRIDES = {
    # "ui": "eks:retail-store/prod-frontend",
}

JVM_SERVICES = ["ui", "carts", "orders"]        # services that report JVM runtime metrics

# Backing services shown on the application-health drilldown (set to None to skip a widget).
DDB_TABLE = "retail-store-carts"
ALB = "app/k8s-ui-ui-e83b75e830/4a28dba7fef82796"
TARGET_GROUP = "targetgroup/k8s-ui-ui-9dc5016786/12993274eec875f4"

# Alarms created by alarms.sh and listed on the NOC alarm widget.
SNS_TOPIC_ARN = f"arn:aws:sns:{REGION}:{ACCOUNT}:noc-alerts"
ALARM_NAMES = [f"{CLUSTER}-appsignals-heartbeat",
               f"{CLUSTER}-otel-heartbeat",
               f"{CLUSTER}-node-not-ready"]

# Container Insights application log group used by the Logs Insights widget.
APP_LOG_GROUP = f"/aws/containerinsights/{CLUSTER}/application"

# Version the dashboards were validated against. Printed by the probe with every report.
ADDON_VERSION_TESTED = "v6.4.0-eksbuild.1"

# ------------------------------------------------------------------ Derived values (do not edit)
SERVICES = list(SERVICE_NAMESPACES)
DASHBOARDS = [f"{CLUSTER}-noc", f"{CLUSTER}-eks-monitoring",
              f"{CLUSTER}-application-health", f"{CLUSTER}-incident-response"]
PROMQL_ENDPOINT = f"https://monitoring.{REGION}.amazonaws.com/api/v1/query"
CONSOLE = (f"https://{REGION}.console.aws.amazon.com/cloudwatch/home"
           f"?region={REGION}#dashboards/dashboard/")
ALARM_ARNS = [f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{n}" for n in ALARM_NAMES]


def env(service):
    """Application Signals Environment dimension value for a service on this cluster."""
    if service in ENVIRONMENT_OVERRIDES:
        return ENVIRONMENT_OVERRIDES[service]
    return f"eks:{CLUSTER}/{SERVICE_NAMESPACES[service]}"


def shell_exports():
    """The values the .sh scripts need, as `export NAME=value` lines."""
    values = {"REGION": REGION, "ACCOUNT": ACCOUNT, "CLUSTER": CLUSTER,
              "SNS_TOPIC_ARN": SNS_TOPIC_ARN, "UI_ENVIRONMENT": env("ui"),
              "DASHBOARDS": " ".join(DASHBOARDS), "ALARM_NAMES": " ".join(ALARM_NAMES),
              "ADDON_VERSION_TESTED": ADDON_VERSION_TESTED}
    return "\n".join(f"export {k}='{v}'" for k, v in values.items())


if __name__ == "__main__":
    print(shell_exports())
