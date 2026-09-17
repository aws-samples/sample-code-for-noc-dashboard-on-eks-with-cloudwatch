---
inclusion: always
---

# EKS NOC dashboard sample

This folder builds, validates and deploys a CloudWatch NOC for Amazon EKS. Before changing anything,
read `.kiro/skills/eks-noc-dashboard/SKILL.md` (file map, deploy order, customization recipes,
verification checklist). The non-negotiable rules:

1. Environment values live only in `config.py`. Never hand-edit a generated `_body.json`.
2. Every PromQL query carries the cluster selector `CL`; use the `Canvas` helpers, not raw widget JSON.
3. Zero-fill counters and state conditions; never zero-fill latency or utilization.
4. Application Signals `Environment` on EKS is `eks:<cluster>/<namespace>`; always go through `config.env()`.
5. After any change: `python3 -m unittest discover -s tests`, then `python3 probe.py <dashboard> 180`.
   `FAILED` blocks; every `EMPTY` needs an explanation; report `BODY_SHA256` and `ADDON_VERSION_TESTED`.
