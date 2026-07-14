---
name: task-knowledge-audit
description: "Execute a delegated store-health phase inside the distill-task-knowledge plugin tree by reporting status, recovering incomplete transactions, validating integrity, or regenerating derived indexes and catalog data without semantic mutation. Use when the root supplies the engine and knowledge root, or when the user explicitly supplies those inputs and requests one audit action. Do not record tasks or curate knowledge."
---

# Task knowledge audit

Own store health. Return to the root; never invoke a sibling or make semantic decisions.

## Resolve inputs

Require `root` and the engine from the root, or use
`{baseDir}/../../scripts/knowledge_store.py`. Use Python 3.12 on one POSIX local host. Multi-host
writers require an external distributed lock.

## Preflight and final gate

```bash
python3.12 <engine> --root <root> status
```

Healthy means `ok` is true, `derived_health.ok` is true, and
`incomplete_transactions`, `record_reconciliations`, `stranded_records`, and
`candidate_integrity_issues` are all empty. Run this before `start` and again after `apply-plan`,
before archive.

## Recover

```bash
python3.12 <engine> --root <root> recover
```

Use `--force` only after externally proving a remote or unknown-host lock is stale. Never override
a live local lock. Preserve ambiguous artifacts and return the exact error.

## Validate or repair derived data

```bash
python3.12 <engine> --root <root> doctor
python3.12 <engine> --root <root> doctor --repair
```

Use the read-only form by default. `--repair` may regenerate only `_index.md` and catalog data; it
must not alter semantic notes.

## Return health

Return root, derived health, incomplete transactions, reconciliations, stranded records, candidate
integrity issues, recovery actions, and repair flag. Do not expose record contents.

## Preserve ownership

- Do not run record, context, candidate, plan, or archive commands.
- Do not manually delete locks, transactions, records, notes, indexes, or catalog files.
- Do not claim health while any incomplete or ambiguous state remains.
