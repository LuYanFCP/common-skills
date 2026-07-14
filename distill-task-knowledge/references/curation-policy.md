# Knowledge-tree curation policy

## Contents

- [Action selection](#action-selection)
- [Tree organization](#tree-organization)
- [Merge policy](#merge-policy)
- [Split policy](#split-policy)
- [Conflict and obsolescence](#conflict-and-obsolescence)

## Action selection

Choose semantic actions only after reading the persisted candidate bundle and existing notes:

| Situation | Action |
| --- | --- |
| No equivalent unit exists | `create` |
| Existing meaning is unchanged; new independent evidence exists | `reinforce` |
| Same unit needs corrected or materially expanded meaning | `update` |
| Multiple units express one compatible rule | `merge` |
| One unit contains independently retrievable rules | `split` |
| Meaning is sound but taxonomy/path is wrong | `move` |
| A rule is demonstrably obsolete in scope | `deprecate` |
| Same-scope claims conflict and evidence cannot decide | `conflict` |
| No semantic mutation is warranted | empty plan / `no-knowledge` |

Do not use `update` to hide disagreement, `merge` because tags match, or `create` to avoid reading
an existing note. Preserve candidate scope, confidence, evidence, and exclusions.

Bind every candidate to exactly one disposition: `applied`, `represented`, or `rejected`.
`applied` cites operation IDs; the other dispositions require a reason. Tree-only maintenance
operations may be unbound when they do not introduce a candidate claim.

## Tree organization

Use semantic paths such as `<domain>/<problem>/<rule>.md`. Do not organize by task date, agent,
sprint, or source record. A project name is appropriate only when the knowledge cannot apply
outside that project.

For every curation:

1. Search catalog title, summary, scope, and tags.
2. Read likely equivalent or conflicting notes in full.
3. Inspect the candidate directory, parent, and siblings. When a directory does not exist, inspect
   the nearest existing parent and record the verified absence.
4. Decide whether to reuse an existing path, add a semantic branch, or repair a local duplicate or
   misplacement.
5. Make only justified local reorganization; avoid global churn during a small task.

Express directory consolidation or partitioning as note operations. The engine removes empty
directories and regenerates indexes while preserving redirects.

Review a directory around 20 direct items, indistinguishable subdirectories, or an “and/or” scope.
These are review triggers, not automatic restructuring rules. Avoid directory oscillation; prefer
multiple records of evidence before renaming a broad branch.

## Merge policy

Merge only when:

- core conclusions or procedural outcomes are equivalent;
- scopes are equal or compatibly representable;
- differences are evidence, wording, examples, or complementary boundaries;
- one precise title covers the result;
- provenance, exceptions, confidence, and useful examples remain intact.

Do not merge platform/version variants that demand different actions. Do not merge a procedure and
failure mode merely because they concern the same tool.

Keep the most stable ID—normally the clearer, older, or more referenced source. Other IDs become
aliases and paths become redirects. Never silently delete a source.

## Split policy

Split when:

- conclusions can be applied independently;
- platform, version, environment, or scope variants require different actions;
- facts, procedures, rules, and failure modes evolve independently;
- no accurate title covers the whole body;
- an exception becomes a reusable rule with its own applicability boundary.

Word count is only a review prompt. Split a short note when it has distinct retrieval targets.
Exactly one target inherits the source ID; independent targets receive deterministic IDs. Preserve
the old path as a collection redirect when it is no longer a target.

## Conflict and obsolescence

Never use last-write-wins.

1. Test whether platform, version, configuration, time, or scope explains disagreement; split
   scoped variants when it does.
2. Compare evidence strength: direct verification, authoritative source, independent records,
   single observation, then inference.
3. Deprecate or update when stronger evidence proves the old rule obsolete while preserving
   provenance.
4. Use `conflict` when same-scope evidence cannot decide; keep both notes contested.
5. Treat an input hash mismatch as concurrency, not semantic evidence. Stop and rebase.
