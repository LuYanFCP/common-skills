---
name: task-knowledge
description: "Orchestrate the complete task-learning plugin tree: audit the store, record one substantive task, distill a sealed candidate bundle, curate the filesystem knowledge tree, re-audit health, and archive before the final response. Use as the single implicit root for successful, failed, or cancelled coding, research, debugging, and operations tasks in a workspace adopting this lifecycle. Guaranteed invocation still requires a host-level always-on instruction or lifecycle hook."
---

# Task knowledge

Act as the only root orchestrator. Hold shared state and delegate each phase to one child. A child
must return here and must never invoke a sibling.

Delegation means reading and following the child `SKILL.md` for that phase in the current agent; it
does not require spawning a collaboration subagent.

```text
task-knowledge
├── task-knowledge-audit
├── task-knowledge-record
├── task-knowledge-distill
└── task-knowledge-curate
```

The Codex plugin manifest installs this tree atomically. Stop if any linked child or shared resource
is missing.

## Fix shared state

Resolve once and pass unchanged:

- `engine`: `{baseDir}/../../scripts/knowledge_store.py`
- `root`: user path, otherwise `TASK_KNOWLEDGE_ROOT`, otherwise `<workspace>/.knowledge`
- `workspace`: task workspace
- `task_id`: returned by the record phase

Run Python 3.12 on a POSIX local filesystem. Never write runtime knowledge into the plugin.

## Route the lifecycle

1. Read [task-knowledge-audit](../task-knowledge-audit/SKILL.md) and run preflight. Recover before
   starting when the store is unhealthy.
2. Read [task-knowledge-record](../task-knowledge-record/SKILL.md) and start exactly one record
   after scope is clear but before substantive work.
3. Perform the actual task. Return to the record child only for meaningful checkpoint events. Use
   one record across internal subagents.
4. After work and verification, but before the final response, return to record and seal as
   `completed`, `failed`, or `cancelled`.
5. Read [task-knowledge-distill](../task-knowledge-distill/SKILL.md). Build sealed context, derive
   `0..N` candidates, and persist one digest-bound candidate bundle.
6. Read [task-knowledge-curate](../task-knowledge-curate/SKILL.md). Review the tree, bind every
   candidate to a disposition, check one plan, and apply it transactionally.
7. Return to audit unconditionally. Require healthy `status` and `doctor`; recover when necessary.
8. Return to record and archive only after both a terminal distillation result and healthy audit.
9. Send the final user response after archival.

Failed or cancelled work follows the same route. Zero knowledge is valid; fabricated knowledge is
not.

## Enforce handoffs

| From | Required handoff |
| --- | --- |
| audit | derived health plus incomplete transaction, reconciliation, stranded-record, and candidate-integrity lists |
| record | `root`, `task_id`, task status, and record path |
| distill | context path, `record_digest`, candidate path, and `candidate_digest` |
| curate | plan hash, bound candidate digest, operation count, and terminal result |

Reject mismatched task, record, candidate, or tree digests. Rebuild context and rebase instead of
overwriting concurrent changes.

## Preserve boundaries

- Record owns factual history only.
- Distill owns candidate extraction and promotion rules only.
- Curate owns taxonomy, candidate disposition, and semantic operations only.
- Audit owns transaction recovery, candidate-chain integrity checks, and derived-file health only.
- All mutations use the shared engine. Never edit records, notes, `_index.md`, or catalog files.

## Report completion

Mention distillation only when useful: terminal status, operation count, and unresolved recovery
issues. Do not expose sensitive record contents.
