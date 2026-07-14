---
name: distill-task-knowledge
description: Orchestrate an auditable task-learning tree that records every substantive task, distills sealed evidence, curates a filesystem knowledge tree, and archives the record before the final response. Use as the single root entry for complete tasks in a workspace adopting this lifecycle. This top-level entry preserves compatibility with skill installers that do not load Codex plugin manifests; guaranteed invocation still requires a host-level always-on instruction or lifecycle hook.
---

# Distill task knowledge

Use the canonical root node at [skills/task-knowledge/SKILL.md](skills/task-knowledge/SKILL.md).
Read it now and
follow its phase routing exactly.

This directory is both a cross-host Skill package and a Codex plugin tree:

```text
distill-task-knowledge/
├── .codex-plugin/plugin.json
├── SKILL.md                         compatibility entry
├── skills/
│   ├── task-knowledge/              canonical root orchestrator
│   ├── task-knowledge-audit/
│   ├── task-knowledge-record/
│   ├── task-knowledge-distill/
│   └── task-knowledge-curate/
├── scripts/knowledge_store.py       shared deterministic engine
└── references/                      shared contracts and policies
```

For this compatibility entry, supply `{baseDir}/scripts/knowledge_store.py` as the absolute engine
path to every phase. Plugin-installed nodes resolve the same engine from the plugin root.

Stop if any listed node, the engine, or a referenced contract is missing. Do not silently run a
partial lifecycle.
