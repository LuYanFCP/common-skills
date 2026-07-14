---
name: task-knowledge-record
description: "Execute only a delegated factual-record phase inside the distill-task-knowledge plugin tree: start one task record, append immutable checkpoints, seal its outcome, or archive it after terminal distillation and a healthy store check. Use when the root orchestrator delegates one of those actions, or when the user explicitly supplies the plugin, knowledge root, and requested record action. Do not derive knowledge or repair the store."
---

# Task knowledge record

Own factual history for one task. Return to the root after each requested action; never invoke a
sibling skill.

## Resolve inputs

Require `root` and the absolute engine path from the root. If explicitly invoked inside the plugin,
use `{baseDir}/../../scripts/knowledge_store.py`. Stop if it is missing. Use Python 3.12.

## Start

After scope is clear and before substantive work:

```bash
python3.12 <engine> --root <root> start \
  --title <short-title> --objective <objective> --workspace <workspace>
```

Return the exact `task_id` and record path. The engine refuses to start while recovery work is
pending. Use one top-level record across subagents; represent them with `--span-id`. For a follow-up
to an archived task, pass `--parent-task-id`.

## Append checkpoints

```bash
python3.12 <engine> --root <root> log \
  --task <task-id> --kind <kind> --text <fact> [--evidence <stable-reference>]
```

Use `context`, `constraint`, `observation`, `decision`, `attempt`, `result`, `verification`, or
`candidate`. Capture concise facts, reasons, failed attempts, evidence, and verification—not full
conversation, secrets, personal data, or large transient output.

For shell metacharacters such as backticks or `$()`, use an argument-array API or robust quoting.
Never interpolate untrusted event text into a shell command.

## Seal

After implementation and verification, exactly once:

```bash
python3.12 <engine> --root <root> seal \
  --task <task-id> --status <completed|failed|cancelled> \
  --summary <outcome> [--remaining <open-item>]
```

Return sealed task status and `pending` distillation status. Do not append events after sealing.

## Archive

After curate returns `committed` or `no-knowledge` and audit reports healthy:

```bash
python3.12 <engine> --root <root> archive --task <task-id>
```

The engine rechecks store health under its write lock before moving the record. Return the archive
path. Never archive an active or non-terminal record.

## Preserve ownership

- Use only `start`, `log`, `seal`, and `archive`.
- Do not edit record JSON or event files manually.
- Do not run context, candidate, plan, recovery, or doctor commands.
