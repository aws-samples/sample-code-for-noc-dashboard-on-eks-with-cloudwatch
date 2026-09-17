"""Offline tests for the dashboard builder. No AWS credentials needed.

    python -m unittest discover -s tests -v        (or: pytest tests)

These catch the defects that a successful PutDashboard would not: a wrong Application Signals
Environment, a query that forgot the cluster selector, a counter that is not zero-filled, a
Logs Insights widget that reads the wrong log group, or a legend that fell back to __verbose__.
"""
import importlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import noc_dashboard  # noqa: E402

EXPECTED_WIDGETS = {"noc": 35, "eks-monitoring": 9, "application-health": 11, "incident-response": 12}


def build_into_temp():
    out = pathlib.Path(tempfile.mkdtemp())
    noc_dashboard.build_all(out)
    bodies = {n: json.loads((out / f"{config.CLUSTER}-{n}_body.json").read_text()) for n in EXPECTED_WIDGETS}
    manifests = {n: {kind: json.loads((out / f"{config.CLUSTER}-{n}_{kind}.json").read_text())
                     for kind in ("promql", "classic", "logs")} for n in EXPECTED_WIDGETS}
    return bodies, manifests


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bodies, cls.manifests = build_into_temp()

    def test_widget_counts_match_the_post(self):
        for name, expected in EXPECTED_WIDGETS.items():
            self.assertEqual(len(self.bodies[name]["widgets"]), expected, name)

    def test_bodies_stay_under_budget_and_inherit_periods(self):
        for name, body in self.bodies.items():
            self.assertLess(len(json.dumps(body, separators=(",", ":"))),
                            noc_dashboard.Canvas.BODY_BUDGET_BYTES, name)
            self.assertEqual(body["periodOverride"], "inherit")

    def test_every_promql_query_is_cluster_scoped(self):
        for name, m in self.manifests.items():
            for entry in m["promql"]:
                self.assertIn(noc_dashboard.CL, entry["query"], f"{name}: {entry['id']}")

    def test_no_verbose_legends(self):
        for name, body in self.bodies.items():
            for w in body["widgets"]:
                if w["type"] == "chart":
                    for q in w["properties"]["data"]["queries"]:
                        self.assertNotEqual(q.get("label"), "__verbose__", name)

    def test_incident_counters_are_zero_filled(self):
        incident = self.manifests["incident-response"]["promql"]
        counters = [e for e in incident if e["id"].startswith("ir_") and e["id"] not in
                    ("ir_pressure", "ir_alloc", "ir_running")]
        self.assertGreaterEqual(len(counters), 6)
        for entry in counters:
            self.assertTrue(entry["query"].endswith("or vector(0)"), entry["id"])

    def test_application_signals_environment_uses_namespace_not_service_name(self):
        for name, m in self.manifests.items():
            for ref in m["classic"]:
                if ref["namespace"] != "ApplicationSignals" or "Service" not in ref["dimensions"]:
                    continue
                service = ref["dimensions"]["Service"]
                expected = f"eks:{config.CLUSTER}/{config.SERVICE_NAMESPACES[service]}"
                self.assertEqual(ref["dimensions"]["Environment"], expected, f"{name}: {ref['widget']}")

    def test_environment_follows_namespace_when_it_differs_from_service_name(self):
        """Regression: env() must not assume namespace == service name."""
        with mock.patch.dict(config.SERVICE_NAMESPACES, {"ui": "frontend"}), \
                mock.patch.dict(config.ENVIRONMENT_OVERRIDES, {}, clear=True):
            self.assertEqual(config.env("ui"), f"eks:{config.CLUSTER}/frontend")
        with mock.patch.dict(config.ENVIRONMENT_OVERRIDES, {"ui": "eks:retail-store/prod-frontend"}):
            self.assertEqual(config.env("ui"), "eks:retail-store/prod-frontend")

    def test_log_widget_reads_the_configured_group_with_source_syntax(self):
        body = self.bodies["incident-response"]
        log_widgets = [w for w in body["widgets"] if w["type"] == "log"]
        self.assertEqual(len(log_widgets), 1)
        query = log_widgets[0]["properties"]["query"]
        self.assertTrue(query.startswith(f"SOURCE '{config.APP_LOG_GROUP}'"), query)
        self.assertNotIn("logGroupNames", json.dumps(log_widgets[0]))  # CDK-only field
        self.assertEqual(self.manifests["incident-response"]["logs"][0]["log_groups"], [config.APP_LOG_GROUP])
        self.assertEqual(self.manifests["noc"]["logs"], [])           # the NOC has no log widget

    def test_noc_and_drilldowns_share_counter_queries(self):
        noc = {e["id"]: e["query"] for e in self.manifests["noc"]["promql"]}
        for drill in ("eks-monitoring", "incident-response"):
            for e in self.manifests[drill]["promql"]:
                if e["id"] in noc:
                    self.assertEqual(noc[e["id"]], e["query"], e["id"])

    def test_config_shell_exports_carry_the_derived_environment(self):
        exports = importlib.reload(config).shell_exports()
        self.assertIn(f"export UI_ENVIRONMENT='eks:{config.CLUSTER}/{config.SERVICE_NAMESPACES['ui']}'", exports)
        self.assertIn(f"export REGION='{config.REGION}'", exports)


if __name__ == "__main__":
    unittest.main()
