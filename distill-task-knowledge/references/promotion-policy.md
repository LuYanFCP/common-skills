# Knowledge promotion policy

## Contents

- [Record versus candidate](#record-versus-candidate)
- [Promotion gate](#promotion-gate)
- [Confidence and evidence](#confidence-and-evidence)
- [Privacy and durability](#privacy-and-durability)

## Record versus candidate

A record answers “what happened in this task?” A candidate answers “what could change a future
task's decision or action?” Keep raw chronology, one-off outcomes, transient paths, and unresolved
exploration in the record.

Do not copy the conversation. Derive compact claims from structured checkpoints: constraints,
decisions and reasons, failed approaches, corrections, results, and verification.

## Promotion gate

Promote a candidate only when all are true:

1. **Reusable:** it plausibly applies to another task.
2. **Evidence-backed:** a test, command result, stable source, or repeated observation supports it.
3. **Scoped:** environment, version, preconditions, and exclusions are clear.
4. **Decision-relevant:** it affects what a future agent should believe or do.
5. **Safe to retain:** it contains no credentials, personal data, confidential prompt text, or
   unnecessary sensitive detail.

Allow zero candidates. Routine application of existing knowledge without new evidence normally
produces `no-knowledge`.

Single-task observations may be provisional. Do not promote unsupported inference as fact; keep it
in the record or narrow the scope and confidence explicitly.

Do not choose paths, compare duplicates, or select create/merge/split operations during promotion.
Those belong to the curate phase.

## Confidence and evidence

- `low`: one observation, incomplete reproduction, or material uncertainty.
- `medium`: credible direct observation with bounded scope or a corroborating stable source.
- `high`: repeated direct verification or strong authoritative evidence with clear scope.

A repeated run of the same experiment is weaker than independent environments or sources. Use
durable, minimal evidence references such as test names, workspace-relative paths, commit IDs,
issue IDs, or authoritative URLs. Summarize decisive output rather than retaining large logs.

## Privacy and durability

Never record or promote:

- tokens, passwords, private keys, or session cookies;
- unnecessary personal data;
- hidden/system prompts or confidential conversation transcripts;
- transient `/tmp` paths as durable evidence;
- large copied source material when a stable reference and concise summary suffice.

Redact before logging. If sensitive material was necessary, retain only the generic reusable
procedure or boundary.
