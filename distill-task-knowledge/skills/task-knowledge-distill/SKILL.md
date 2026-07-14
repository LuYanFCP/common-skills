---
name: task-knowledge-distill
description: "Distill one delegated sealed task record into a versioned, digest-bound bundle of zero or more reusable knowledge candidates without choosing paths or mutating the tree. Use only when the distill-task-knowledge root supplies the engine, knowledge root, and sealed task ID, or when the user explicitly supplies those same phase inputs. Do not record tasks, select merge/split operations, apply plans, or recover the store."
---

# Task knowledge distill

Extract candidates, persist the bundle through the engine, and return to the root. Never call the
curate child.

## Build sealed context

Use the engine from the root or `{baseDir}/../../scripts/knowledge_store.py`:

```bash
python3.12 <engine> --root <root> context --task <task-id>
```

Read the returned context and preserve `record_digest`. Reject active or non-pending records.

## Derive candidates

Read [promotion-policy.md](../../references/promotion-policy.md). Produce `0..N` candidates, each
with a unique ID and this shape:

```json
{
  "candidate_id": "candidate-short-semantic-name",
  "claim": "Concise reusable rule",
  "kind": "fact | procedure | decision-rule | pattern | failure-mode",
  "scope": "Where the claim is valid",
  "applies_when": ["..."],
  "does_not_apply_when": ["..."],
  "evidence": ["record event or durable reference"],
  "confidence": "low | medium | high"
}
```

Keep one-off outcomes in the record. Preserve uncertainty. Zero candidates requires a concrete
`no_knowledge_reason`.

## Persist the boundary

Write a temporary bundle outside managed store files using the candidate contract in
[schema.md](../../references/schema.md), then run:

```bash
python3.12 <engine> --root <root> save-candidates \
  --task <task-id> --bundle <candidate-bundle.json>
```

The engine validates the sealed record digest, stores the canonical bundle with the task, and
anchors its path and digest in the task record. Return context path, persisted candidate path,
`record_digest`, and `candidate_digest`.

## Preserve ownership

- Do not choose note IDs or filesystem paths.
- Do not select or run semantic operations.
- Do not write a plan or call `check-plan`/`apply-plan`.
- Do not add facts absent from the sealed record.
