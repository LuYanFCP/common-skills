---
name: task-knowledge-curate
description: "Curate one delegated, persisted candidate bundle into the filesystem knowledge tree by selecting semantic operations and checking and applying one transactional plan. Use only when the distill-task-knowledge root supplies the engine, root, task ID, sealed context, candidate path, and candidate digest, or when the user explicitly supplies that complete phase handoff. Do not create task facts or repair transaction state."
---

# Task knowledge curate

Own taxonomy and candidate disposition. Return one terminal result to the root; never invoke a
sibling.

## Resolve inputs

Require `root`, `task_id`, context path, persisted candidate path, `record_digest`, and
`candidate_digest`. Use the root engine or `{baseDir}/../../scripts/knowledge_store.py`.

Read:

- [schema.md](../../references/schema.md)
- [curation-policy.md](../../references/curation-policy.md)

## Review the tree

Inspect relevant notes, the candidate directory, its parent and siblings, and plausible global
duplicates. Select `create`, `reinforce`, `update`, `merge`, `split`, `move`, `deprecate`, or
`conflict` by meaning and retrieval boundary. Express directory cleanup through note operations;
the engine removes empty directories and regenerates indexes.

When a proposed directory does not exist, record its nearest existing parent plus the verified
absence of the proposed path in `tree_review`.

## Bind one plan

Write one plan outside managed store files. Copy from the same handoff:

- `expected_record_digest`
- `expected_candidate_digest`
- `expected_tree_digest`
- exact `expected_sha256` for existing notes touched

Add `candidate_bindings` so every candidate ID has exactly one disposition and optional operation
IDs. Use `applied` with at least one operation, or `represented`/`rejected` with a reason. Tree-only
cleanup operations may remain unbound. For zero candidates, use an empty binding list.

## Check and apply

```bash
python3.12 <engine> --root <root> check-plan \
  --task <task-id> --plan <plan.json>

python3.12 <engine> --root <root> apply-plan \
  --task <task-id> --plan <plan.json>
```

Run `apply-plan` only after checking succeeds. Digest mismatches require rereading and rebasing, not
overwriting. Return candidate digest, plan hash, operation count, touched paths, and terminal
status. Route recovery-required results back to the root.

## Preserve ownership

- Do not introduce claims absent from the persisted bundle and sealed context.
- Do not edit store files directly.
- Do not run recovery, repair, or archive commands.
