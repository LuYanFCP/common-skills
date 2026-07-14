# Filesystem and plan contracts

## Contents

- [Store layout](#store-layout)
- [Task lifecycle](#task-lifecycle)
- [Candidate bundle](#candidate-bundle)
- [Knowledge-note format](#knowledge-note-format)
- [Distillation plan](#distillation-plan)
- [Note specification](#note-specification)
- [Operations](#operations)
- [Generated outputs](#generated-outputs)
- [Concurrency and recovery](#concurrency-and-recovery)

## Store layout

The default root is `<workspace>/.knowledge`; callers may override it with `--root` or
`TASK_KNOWLEDGE_ROOT`.

```text
<root>/
├── records/
│   ├── active/<task-id>/
│   │   ├── task-record.json
│   │   ├── events/<event-id>.json
│   │   ├── distillation-context.json
│   │   ├── candidate-bundle.json
│   │   ├── distillation-plan.json
│   │   └── distillation-result.json
│   └── archive/YYYY/MM/DD/<task-id>/...
├── knowledge/
│   ├── _index.md
│   └── <domain>/<problem>/<knowledge-slug>.md
└── .meta/
    ├── schema-version
    ├── catalog.json
    ├── locks/knowledge-write/
    └── transactions/<transaction-id>/
        ├── status.json
        ├── plan.json
        ├── result.json
        ├── stage/
        └── before/
```

Task records are organized by time after archival. Knowledge is organized by meaning and must not
include dates merely because its source record is dated. `_index.md` and `catalog.json` are derived
and must only be changed by the script.

## Task lifecycle

Task state and distillation state are separate:

```text
task:          active -> completed | failed | cancelled
distillation:  not-ready -> pending -> planned -> committed | no-knowledge
                                      -> recovery-required (exceptional)
```

A record's factual fields and events are mutable only while the task is `active`; distillation and
archive bookkeeping remains mutable afterward. `seal` freezes facts and moves the distillation
state to `pending`. `context`, `save-candidates`, `check-plan`, and `apply-plan` are accepted only
for a sealed task in that pending state. `apply-plan` then commits a distillation or records why no
durable knowledge exists; an exact replay of its committed plan remains idempotent. `archive` is
permitted only after either terminal distillation state and an atomic healthy-store check.

The context includes `record_digest`, a canonical SHA-256 over the sealed factual record fields and
ordered event objects. It deliberately excludes mutable bookkeeping such as `updated_at`,
`distillation_status`, result links, and archive timestamps. A plan must bind that factual
snapshot, the persisted candidate bundle, and the reviewed knowledge tree. If any digest changes,
rebuild the affected boundary and rebase rather than applying stale conclusions.

Event files are immutable and independent so parallel subagents do not contend on a shared JSONL
append. A `span_id` may identify the contributing subagent or workstream.

## Candidate bundle

After `context`, the distillation phase writes a temporary UTF-8 JSON bundle and persists it with
`save-candidates --task <task-id> --bundle <path>`. The command validates the sealed record binding,
normalizes the bundle, stores it as `candidate-bundle.json`, and returns its canonical SHA-256 as
`candidate_digest`. A task may persist only one semantic bundle; an exact save is idempotent and a
different replacement is rejected. On the first save, the command durably anchors
`candidate_bundle: "candidate-bundle.json"` and `candidate_digest` in `task-record.json` before
writing the bundle. Every later load and save checks that anchor; a modified anchored bundle is an
integrity failure and cannot be made valid by re-running `save-candidates`.

```json
{
  "schema_version": "distill-task-knowledge.candidates.v1",
  "task_id": "task-flaky-test-fix-20260714-120000-abcdef",
  "record_digest": "<distillation-context.record_digest>",
  "candidates": [
    {
      "candidate_id": "candidate-bound-retries",
      "claim": "Bound retries and stop after the configured attempt limit.",
      "kind": "decision-rule",
      "scope": "Transient test failures with an explicit retry budget",
      "applies_when": ["The failure is classified as transient"],
      "does_not_apply_when": ["The failure is deterministic"],
      "evidence": ["event-... verified the bounded retry behavior"],
      "confidence": "high"
    }
  ],
  "no_knowledge_reason": null
}
```

Candidate IDs start with `candidate-`, contain lowercase letters, digits, and hyphens, and are
unique in the bundle. `claim`, `kind`, `scope`, `applies_when`, `does_not_apply_when`, `evidence`,
and `confidence` are required; evidence must contain at least one item. Kinds and confidence use
the same enums as notes. A bundle contains `0..N` candidates. Zero candidates requires a concrete
`no_knowledge_reason`; when candidates exist that field must be null, empty, or omitted.

## Knowledge-note format

Each knowledge file is Markdown with a JSON object between YAML-compatible frontmatter markers:

```markdown
---
{
  "schema_version": "distill-task-knowledge.note.v1",
  "id": "k-0123456789abcdef",
  "title": "Verify generated indexes after tree changes",
  "summary": "Regenerate and validate derived indexes after semantic moves.",
  "kind": "procedure",
  "status": "verified",
  "scope": "Filesystem knowledge-tree transactions",
  "applies_when": ["A note is created, moved, merged, or split"],
  "does_not_apply_when": ["Only a task record event is appended"],
  "confidence": "high",
  "tags": ["knowledge-management", "validation"],
  "source_records": ["task-example-20260714-120000-abcdef"],
  "related": [],
  "supersedes": [],
  "conflicts_with": [],
  "aliases": [],
  "created_at": "2026-07-14T04:00:00Z",
  "updated_at": "2026-07-14T04:00:00Z",
  "revision": 1
}
---

# Verify generated indexes after tree changes

## Rule

...
```

The script owns IDs, timestamps, revisions, aliases, and source-record linkage. Plans provide
semantic fields and bodies. Moved IDs stay stable. Merged-away IDs become aliases, and old paths
become redirects instead of disappearing silently.

Kinds are `fact`, `procedure`, `decision-rule`, `pattern`, and `failure-mode`. Statuses are
`provisional`, `verified`, `contested`, and `deprecated`. Confidence is `low`, `medium`, or `high`.

## Distillation plan

Write UTF-8 JSON with this top-level shape:

```json
{
  "schema_version": "distill-task-knowledge.plan.v1",
  "plan_id": "distill-flaky-test-fix-v1",
  "task_id": "task-flaky-test-fix-20260714-120000-abcdef",
  "expected_record_digest": "<distillation-context.record_digest: 64 lowercase hex characters>",
  "expected_candidate_digest": "<save-candidates.candidate_digest: 64 lowercase hex characters>",
  "expected_tree_digest": "<catalog.tree_digest: 64 lowercase hex characters>",
  "tree_review": {
    "inspected_paths": [
      "_index.md",
      "engineering/_index.md",
      "engineering/testing/_index.md",
      "engineering/testing/retry-policy.md"
    ],
    "decision": "Reinforce the existing scoped rule; no merge, split, or move is warranted."
  },
  "operations": [],
  "candidate_bindings": [
    {
      "candidate_id": "candidate-bound-retries",
      "disposition": "represented",
      "operation_ids": [],
      "reason": "An existing note already represents the claim and evidence adds no new support."
    }
  ],
  "no_knowledge_reason": "The task only applied an already-documented rule and added no evidence."
}
```

Copy `expected_record_digest` from context, `expected_candidate_digest` from `save-candidates`, and
`expected_tree_digest` from the reviewed catalog. They bind the sealed facts, persisted distillation
handoff, and semantic tree review respectively. Rebuild and rebase whenever one changes.
`inspected_paths` must include at least one existing path relative to `<root>/knowledge`. Normally
inspect the candidate directory, its parent, its siblings, and likely global duplicates found
through catalog titles, summaries, tags, and scopes. If `operations` is empty,
`no_knowledge_reason` is required. If operations exist, `no_knowledge_reason` must be null, empty,
or omitted.

`candidate_bindings` must contain every persisted candidate exactly once. Disposition `applied`
requires one or more existing operation IDs. Dispositions `represented` and `rejected` require a
reason and cannot cite operations. An operation may serve multiple candidates; justified tree-only
cleanup operations may remain unbound. A zero-candidate bundle requires an empty binding list.

Every operation requires:

```json
{
  "operation_id": "op-1",
  "op": "create",
  "reason": "A reusable procedure is not represented in the current tree."
}
```

Operation IDs contain lowercase letters, digits, and hyphens and are unique within the plan.
They also seed deterministic IDs, so do not rename them when retrying an unchanged plan.

## Note specification

`create`, `update`, `merge`, `split`, and `conflict` carry a full note specification:

```json
{
  "title": "Use optimistic hashes for knowledge updates",
  "summary": "Reject a tree mutation when an input note changed after planning.",
  "kind": "decision-rule",
  "status": "verified",
  "scope": "Concurrent filesystem knowledge maintenance",
  "applies_when": ["A plan updates, moves, merges, splits, or deprecates an existing note"],
  "does_not_apply_when": ["Creating a path that is known not to exist"],
  "confidence": "high",
  "tags": ["concurrency", "knowledge-management"],
  "related": [],
  "supersedes": [],
  "conflicts_with": [],
  "body": "## Rule\n\nCarry the catalog SHA-256 into the plan..."
}
```

Required fields are `title`, `summary`, `kind`, `scope`, and `body`. Defaults are
`status=provisional`, `confidence=medium`, and empty lists. All referenced IDs in `related`,
`supersedes`, and `conflicts_with` must resolve after the full plan is staged. Do not provide IDs,
source records, timestamps, revisions, or aliases; the script preserves or creates them.

Knowledge paths are relative to `<root>/knowledge`, use `/`, end in `.md`, and cannot contain
absolute components, `..`, hidden components, or the reserved `_index.md` filename.

## Operations

Hashes below come verbatim from `distillation-context.json` at `catalog.notes[].sha256`.

### Create

```json
{
  "operation_id": "op-create-locking-rule",
  "op": "create",
  "reason": "No existing note covers concurrent writers.",
  "path": "engineering/knowledge-management/optimistic-locking.md",
  "note": { "title": "...", "summary": "...", "kind": "decision-rule", "scope": "...", "body": "..." }
}
```

The target must not exist.

### Reinforce

Use when meaning and body stay unchanged but this record adds independent evidence.

```json
{
  "operation_id": "op-reinforce-locking-rule",
  "op": "reinforce",
  "reason": "A second verified task confirms the same scoped rule.",
  "path": "engineering/knowledge-management/optimistic-locking.md",
  "expected_sha256": "<64 lowercase hex characters>",
  "confidence": "high",
  "status": "verified"
}
```

`confidence` and `status` are optional; `reason` is stored as reinforcement provenance.

### Update

Use when the same knowledge unit needs a material semantic revision.

```json
{
  "operation_id": "op-update-locking-rule",
  "op": "update",
  "reason": "New verification adds a necessary stale-lock boundary.",
  "path": "engineering/knowledge-management/optimistic-locking.md",
  "expected_sha256": "<hash>",
  "note": { "title": "...", "summary": "...", "kind": "decision-rule", "scope": "...", "body": "..." }
}
```

The existing stable ID and aliases are preserved.

### Move

Use for taxonomy correction without changing meaning.

```json
{
  "operation_id": "op-move-locking-rule",
  "op": "move",
  "reason": "The note belongs with knowledge-store concurrency rules.",
  "source_path": "engineering/concurrency/optimistic-locking.md",
  "target_path": "engineering/knowledge-management/optimistic-locking.md",
  "expected_sha256": "<hash>"
}
```

The stable ID moves to the target. The source becomes a redirect.

### Merge

Use only after confirming semantic equivalence and compatible scope.

```json
{
  "operation_id": "op-merge-locking-rules",
  "op": "merge",
  "reason": "Both notes state the same rule with complementary evidence.",
  "sources": [
    {"path": "engineering/concurrency/hash-check.md", "expected_sha256": "<hash-a>"},
    {"path": "engineering/knowledge-management/optimistic-locking.md", "expected_sha256": "<hash-b>"}
  ],
  "target_path": "engineering/knowledge-management/optimistic-locking.md",
  "keep_id": "k-id-from-one-source",
  "note": { "title": "...", "summary": "...", "kind": "decision-rule", "scope": "...", "body": "..." }
}
```

`keep_id` must be a source ID. Other source IDs become aliases. Non-target source paths become
redirects.

### Split

Use when a source contains independently retrievable rules. Exactly one target inherits the source
ID.

```json
{
  "operation_id": "op-split-transactions",
  "op": "split",
  "reason": "Lock acquisition and crash recovery change independently.",
  "source_path": "engineering/knowledge-management/transactions.md",
  "expected_sha256": "<hash>",
  "targets": [
    {
      "path": "engineering/knowledge-management/write-locks.md",
      "inherit_source_id": true,
      "note": { "title": "...", "summary": "...", "kind": "procedure", "scope": "...", "body": "..." }
    },
    {
      "path": "engineering/knowledge-management/crash-recovery.md",
      "inherit_source_id": false,
      "note": { "title": "...", "summary": "...", "kind": "procedure", "scope": "...", "body": "..." }
    }
  ]
}
```

If no target retains the old path, the old path becomes a multi-target redirect.

### Deprecate

```json
{
  "operation_id": "op-deprecate-old-rule",
  "op": "deprecate",
  "reason": "Direct verification shows the rule no longer applies after version 3.",
  "path": "engineering/tooling/old-rule.md",
  "expected_sha256": "<hash>"
}
```

The file remains addressable and retains its history.

### Conflict

Use when same-scope claims disagree and available evidence cannot resolve them.

```json
{
  "operation_id": "op-record-runtime-conflict",
  "op": "conflict",
  "reason": "Two verified environments produce incompatible same-scope outcomes.",
  "existing_path": "engineering/runtime/loader-order.md",
  "expected_sha256": "<hash>",
  "candidate_path": "_conflicts/engineering/runtime/loader-order-alternative.md",
  "candidate_note": { "title": "...", "summary": "...", "kind": "fact", "scope": "...", "body": "..." }
}
```

Both notes become `contested` and point to one another by stable ID.

## Generated outputs

All commands emit one versioned JSON object to stdout. Important persisted files are:

- `task-record.json`: task identity, status, objective, outcome, distillation state, and the
  immutable candidate bundle path/digest anchor after the first candidate save.
- `events/*.json`: immutable, timestamped checkpoint facts.
- `distillation-context.json`: sealed task record, events, their `record_digest`, and compact
  knowledge catalog.
- `candidate-bundle.json`: immutable normalized `0..N` candidate handoff bound to the record.
- `distillation-plan.json`: exact plan accepted for the task.
- `distillation-result.json`: transaction ID, canonical plan hash, candidate digest, status, and
  touched paths. Its candidate digest must match the record anchor, persisted canonical bundle,
  and transaction plan; its plan hash must match the canonical transaction `plan.json` bytes.
  Status, operation count, touched paths, no-knowledge reason, and store paths are derived from and
  strictly checked against that same plan and store.
- `.meta/catalog.json`: generated note metadata, aliases, redirects, and current content hashes.
- `knowledge/**/_index.md`: generated local navigation.

In the `check-plan` response, `proposed_knowledge_items` is the total number of real knowledge
notes in the fully staged tree, not the number newly created by that plan.

## Concurrency and recovery

Plans require optimistic digests for the sealed record snapshot, persisted candidate bundle, and
the full knowledge tree; existing-note operations additionally require exact note SHA-256 checks.
Any mismatch aborts
staging before the live tree changes. `check-plan` performs the full staging and validation in a
temporary directory without changing the record or store. `apply-plan` repeats those checks in a
transaction stage, applies every operation, validates IDs and references, rebuilds indexes, then
swaps the directory under a global write lock. An unchanged plan is idempotent through its
canonical plan hash.

The reference implementation targets Python 3.12 on a POSIX local filesystem and uses `fcntl` to
serialize lock-state transitions. The store lock carries an ownership token and refuses to clear a
live local process. A lock from a different or unknown host requires explicit `recover --force`
after external verification that the owner is gone. This remains a single-host design; add an
external distributed lock before allowing multiple hosts to write one network-mounted root.

Normal exceptions restore the previous tree. If the process is killed mid-swap, `recover` examines
the live, staged, and backup trees and either completes an unambiguous swap or restores the prior
tree. It refuses ambiguous states. `--force` only controls stale lock removal; it does not force an
ambiguous tree choice. If the tree commit completed but the process stopped before updating the
task record, `status` reports `record_reconciliations` and `recover` links the committed result back
to that record. A durable `planned` transaction marker is written before the task record enters its
planned state. `status` also reports transaction directories without a status as orphans and
planned records without a matching transaction or result as stranded; `recover` removes only empty
orphans and resets only records that remain demonstrably unowned after a locked recheck.

`status`, `doctor`, `start`, and `archive` share the same health boundary: transaction and record
recovery queues must be empty, every candidate/result/transaction digest chain must validate, and
the catalog plus generated indexes must match the semantic knowledge tree. `doctor --repair` may
rebuild only those derived catalog and index artifacts; it cannot bless a modified candidate bundle
or repair semantic knowledge automatically. A transaction marked committed without its real result
artifact is reported as both incomplete and an integrity failure; recovery refuses to guess a
replacement result or silently reset the owning record.
