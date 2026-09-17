# Agent instructions

If you are an AI coding assistant working in this folder, read `.kiro/skills/eks-noc-dashboard/SKILL.md`
first. It describes the files, the deploy order, the rules that keep the dashboards trustworthy,
and the checks to run before reporting success. The same file works as a steering or rules
document for assistants other than Kiro.

Short version: change `config.py`, never a `_body.json`; run `python3 probe.py --discover` before
the first deploy; rebuild, test and re-probe after every change; treat `FAILED` as blocking and
explain every `EMPTY`.
