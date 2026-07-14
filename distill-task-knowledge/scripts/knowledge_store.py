#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5


STORE_SCHEMA = "distill-task-knowledge.store.v1"
RECORD_SCHEMA = "distill-task-knowledge.record.v1"
EVENT_SCHEMA = "distill-task-knowledge.event.v1"
PLAN_SCHEMA = "distill-task-knowledge.plan.v1"
CANDIDATE_BUNDLE_SCHEMA = "distill-task-knowledge.candidates.v1"
NOTE_SCHEMA = "distill-task-knowledge.note.v1"
CATALOG_SCHEMA = "distill-task-knowledge.catalog.v1"
TRANSACTION_SCHEMA = "distill-task-knowledge.transaction.v1"
RESULT_SCHEMA = "distill-task-knowledge.result.v1"
CANDIDATE_BUNDLE_FILENAME = "candidate-bundle.json"

TASK_STATUSES = {"active", "completed", "failed", "cancelled"}
EVENT_KINDS = {
    "context",
    "constraint",
    "observation",
    "decision",
    "attempt",
    "result",
    "verification",
    "candidate",
}
NOTE_KINDS = {"fact", "procedure", "decision-rule", "pattern", "failure-mode"}
NOTE_STATUSES = {"provisional", "verified", "contested", "deprecated"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
CANDIDATE_DISPOSITIONS = {"applied", "represented", "rejected"}
OPERATION_TYPES = {
    "create",
    "reinforce",
    "update",
    "merge",
    "split",
    "move",
    "deprecate",
    "conflict",
}


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def slugify(value: str, fallback: str = "task") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:48] or fallback


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"JSON file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(tree: Path) -> None:
    assert_no_symlinks(tree)
    for path in sorted(item for item in tree.rglob("*") if item.is_file()):
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    directories = [tree, *(path for path in tree.rglob("*") if path.is_dir())]
    for directory in sorted(
        directories, key=lambda path: len(path.parts), reverse=True
    ):
        fsync_directory(directory)
    fsync_directory(tree.parent)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def emit(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def resolve_root(raw_root: str | None) -> Path:
    configured = raw_root or os.environ.get("TASK_KNOWLEDGE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / ".knowledge").resolve()


def assert_fixed_store_paths(root: Path) -> None:
    fixed_paths = (
        root / ".meta",
        root / ".meta" / "transactions",
        root / ".meta" / "locks",
        root / ".meta" / "schema-version",
        root / ".meta" / "catalog.json",
        root / "knowledge",
        root / "knowledge" / "_index.md",
        root / "records",
        root / "records" / "active",
        root / "records" / "archive",
    )
    for path in fixed_paths:
        if path.is_symlink():
            raise ValueError(f"Fixed knowledge-store path cannot be a symlink: {path}")


def ensure_store(root: Path, *, allow_missing_derived: bool = False) -> None:
    assert_fixed_store_paths(root)
    schema_path = root / ".meta" / "schema-version"
    is_new_store = not schema_path.exists()
    existing_incomplete: list[dict[str, Any]] = []
    if schema_path.exists():
        existing_incomplete = incomplete_transactions(root)
        if not (root / "knowledge").is_dir():
            if existing_incomplete:
                raise RuntimeError(
                    f"Knowledge tree is missing during an incomplete transaction; "
                    f"run recover for {root}"
                )
            raise FileNotFoundError(
                f"Canonical knowledge tree is missing from existing store: {root / 'knowledge'}"
            )
        if not (root / "records").is_dir():
            raise FileNotFoundError(
                f"Canonical records directory is missing from existing store: {root / 'records'}"
            )

    root.mkdir(parents=True, exist_ok=True)
    for path in (
        root / "knowledge",
        root / "records" / "active",
        root / "records" / "archive",
        root / ".meta" / "transactions",
        root / ".meta" / "locks",
    ):
        path.mkdir(parents=True, exist_ok=True)

    if schema_path.exists():
        actual = schema_path.read_text(encoding="utf-8").strip()
        if actual != STORE_SCHEMA:
            raise ValueError(
                f"Unsupported knowledge store schema {actual!r} in {schema_path}; "
                f"expected {STORE_SCHEMA!r}"
            )
    else:
        atomic_write_text(schema_path, STORE_SCHEMA + "\n")

    index_path = root / "knowledge" / "_index.md"
    catalog_path = root / ".meta" / "catalog.json"
    if (not index_path.exists() or not catalog_path.exists()) and existing_incomplete:
        raise RuntimeError(
            f"Derived knowledge files are incomplete during a transaction; run recover for {root}"
        )
    if is_new_store:
        rebuild_indexes(root / "knowledge")
        catalog = validate_and_catalog(root, root / "knowledge")
        atomic_write_json(catalog_path, catalog)
    elif (
        not index_path.is_file() or not catalog_path.is_file()
    ) and not allow_missing_derived:
        raise RuntimeError(
            f"Derived knowledge files are missing; run doctor --repair for {root}"
        )


def validate_task_id(task_id: str) -> str:
    if not re.fullmatch(r"task-[a-z0-9][a-z0-9-]{0,100}", task_id):
        raise ValueError(f"Invalid task id: {task_id!r}")
    return task_id


def validate_transaction_id(transaction_id: str) -> str:
    if not re.fullmatch(r"txn-[a-z0-9][a-z0-9-]{0,159}", transaction_id):
        raise ValueError(f"Invalid transaction id: {transaction_id!r}")
    return transaction_id


def distillation_transaction_id(task_id: str, plan_hash: str) -> str:
    validate_task_id(task_id)
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
        raise ValueError("Plan hash must be a lowercase SHA-256 digest")
    task_token = slugify(task_id, "task")[-40:].strip("-") or "task"
    return validate_transaction_id(f"txn-{task_token}-{plan_hash[:12]}")


def active_task_dir(root: Path, task_id: str) -> Path:
    return root / "records" / "active" / validate_task_id(task_id)


def mkdir_chain_without_symlinks(base: Path, parts: list[str]) -> Path:
    current = base
    if current.is_symlink():
        raise ValueError(f"Base directory cannot be a symlink: {current}")
    for part in parts:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", part):
            raise ValueError(f"Unsafe directory component: {part!r}")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Directory component cannot be a symlink: {current}")
        current.mkdir(exist_ok=True)
        fsync_directory(current.parent)
    return current


def load_active_record(root: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    task_dir = active_task_dir(root, task_id)
    if task_dir.is_symlink():
        raise ValueError(f"Task record directory cannot be a symlink: {task_dir}")
    record_path = task_dir / "task-record.json"
    events_dir = task_dir / "events"
    if record_path.is_symlink() or events_dir.is_symlink():
        raise ValueError(f"Task record components cannot be symlinks: {task_dir}")
    record = read_json(record_path)
    if record.get("schema_version") != RECORD_SCHEMA:
        raise ValueError(f"Unsupported task record schema in {record_path}")
    return task_dir, record


def iter_active_records(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    active_root = root / "records" / "active"
    if active_root.is_symlink():
        raise ValueError(f"Active records directory cannot be a symlink: {active_root}")
    for task_dir in sorted(active_root.iterdir()):
        if task_dir.is_symlink():
            raise ValueError(f"Task record directory cannot be a symlink: {task_dir}")
        if not task_dir.is_dir():
            continue
        task_id = validate_task_id(task_dir.name)
        record_path = task_dir / "task-record.json"
        if record_path.is_symlink():
            raise ValueError(f"Task record cannot be a symlink: {record_path}")
        if not record_path.exists():
            continue
        loaded_dir, record = load_active_record(root, task_id)
        if record.get("task_id") != task_id:
            raise ValueError(
                f"Task record id does not match its directory: {record_path}"
            )
        yield loaded_dir / "task-record.json", record


def save_record(task_dir: Path, record: dict[str, Any]) -> None:
    record["updated_at"] = utc_now()
    atomic_write_json(task_dir / "task-record.json", record)


def load_record_events(task_dir: Path, task_id: str) -> list[dict[str, Any]]:
    event_paths = sorted((task_dir / "events").glob("*.json"))
    if any(path.is_symlink() for path in event_paths):
        raise ValueError(f"Task event files cannot be symlinks: {task_dir / 'events'}")
    events = [read_json(path) for path in event_paths]
    for event in events:
        if event.get("schema_version") != EVENT_SCHEMA:
            raise ValueError(f"Unsupported task event schema for task {task_id}")
        if event.get("task_id") != task_id:
            raise ValueError(f"Task event does not belong to task {task_id}")
    return events


def sealed_record_digest(record: dict[str, Any], events: list[dict[str, Any]]) -> str:
    factual_record = {
        field: record.get(field)
        for field in (
            "schema_version",
            "task_id",
            "title",
            "objective",
            "workspace",
            "parent_task_id",
            "task_status",
            "created_at",
            "sealed_at",
            "event_count",
            "outcome",
        )
    }
    return sha256_bytes(
        canonical_json(
            {
                "schema_version": "distill-task-knowledge.record-snapshot.v1",
                "record": factual_record,
                "events": events,
            }
        )
    )


def require_pending_distillation(
    record: dict[str, Any], task_id: str, action: str
) -> None:
    if record.get("task_status") == "active":
        raise RuntimeError(f"Seal task {task_id} before {action}")
    if record.get("task_status") not in TASK_STATUSES - {"active"}:
        raise RuntimeError(
            f"Task {task_id} has invalid task state {record.get('task_status')!r}"
        )
    if record.get("distillation_status") != "pending":
        raise RuntimeError(
            f"Task {task_id} must have distillation_status='pending' before {action}; "
            f"found {record.get('distillation_status')!r}"
        )


def all_record_ids(root: Path) -> set[str]:
    result: set[str] = set()
    records_root = root / "records"
    for path in records_root.rglob("task-record.json"):
        try:
            record = read_json(path)
        except (FileNotFoundError, ValueError):
            continue
        task_id = record.get("task_id")
        if isinstance(task_id, str):
            result.add(task_id)
    return result


def validate_relative_note_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Knowledge path must be a non-empty string")
    if "\\" in raw_path:
        raise ValueError(f"Knowledge path must use '/' separators: {raw_path!r}")
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ValueError(
            f"Knowledge path must stay inside the knowledge tree: {raw_path!r}"
        )
    if pure.suffix != ".md":
        raise ValueError(f"Knowledge path must end in .md: {raw_path!r}")
    if pure.name == "_index.md" or any(part.startswith(".") for part in pure.parts):
        raise ValueError(f"Knowledge path uses a reserved name: {raw_path!r}")
    return pure.as_posix()


def note_path(tree: Path, relative_path: str) -> Path:
    return tree.joinpath(
        *PurePosixPath(validate_relative_note_path(relative_path)).parts
    )


def parse_note(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Knowledge note has no JSON frontmatter: {path}")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError(f"Knowledge note has unterminated frontmatter: {path}")
    raw_metadata = text[4:marker]
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON frontmatter in {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"Knowledge frontmatter must be an object: {path}")
    if metadata.get("schema_version") != NOTE_SCHEMA:
        raise ValueError(f"Unsupported knowledge note schema in {path}")
    return metadata, text[marker + 5 :].lstrip("\n")


def note_document(metadata: dict[str, Any], body: str) -> str:
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Knowledge note title must be non-empty")
    normalized_body = body.strip()
    if not normalized_body:
        raise ValueError(f"Knowledge note {title!r} must have a non-empty body")
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    return f"---\n{frontmatter}\n---\n\n# {title.strip()}\n\n{normalized_body}\n"


def raw_note_document(metadata: dict[str, Any], raw_content: str) -> str:
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    return f"---\n{frontmatter}\n---\n\n{raw_content.lstrip()}"


def write_note(path: Path, metadata: dict[str, Any], body: str) -> None:
    atomic_write_text(path, note_document(metadata, body))


def write_raw_note(path: Path, metadata: dict[str, Any], raw_content: str) -> None:
    atomic_write_text(path, raw_note_document(metadata, raw_content))


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def string_list(value: Any, label: str, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return list(dict.fromkeys(item.strip() for item in value))


def validate_candidate_bundle(bundle: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Validate and normalize the immutable distillation-to-curation handoff."""
    allowed_bundle_fields = {
        "schema_version",
        "task_id",
        "record_digest",
        "candidates",
        "no_knowledge_reason",
    }
    unexpected_bundle_fields = sorted(set(bundle) - allowed_bundle_fields)
    if unexpected_bundle_fields:
        raise ValueError(
            f"Candidate bundle has unsupported fields: {unexpected_bundle_fields}"
        )
    if bundle.get("schema_version") != CANDIDATE_BUNDLE_SCHEMA:
        raise ValueError(f"Candidate bundle schema must be {CANDIDATE_BUNDLE_SCHEMA!r}")
    if bundle.get("task_id") != task_id:
        raise ValueError(f"Candidate bundle task_id must equal {task_id!r}")
    record_digest = require_string(bundle.get("record_digest"), "record_digest")
    if not re.fullmatch(r"[0-9a-f]{64}", record_digest):
        raise ValueError("record_digest must be a lowercase SHA-256 digest")

    raw_candidates = bundle.get("candidates")
    if not isinstance(raw_candidates, list) or any(
        not isinstance(item, dict) for item in raw_candidates
    ):
        raise ValueError("candidates must be a list of objects")
    allowed_candidate_fields = {
        "candidate_id",
        "claim",
        "kind",
        "scope",
        "applies_when",
        "does_not_apply_when",
        "evidence",
        "confidence",
    }
    normalized_candidates: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    for index, candidate in enumerate(raw_candidates):
        label = f"candidates[{index}]"
        missing_candidate_fields = sorted(allowed_candidate_fields - set(candidate))
        if missing_candidate_fields:
            raise ValueError(
                f"{label} is missing required fields: {missing_candidate_fields}"
            )
        unexpected_candidate_fields = sorted(set(candidate) - allowed_candidate_fields)
        if unexpected_candidate_fields:
            raise ValueError(
                f"{label} has unsupported fields: {unexpected_candidate_fields}"
            )
        candidate_id = require_string(
            candidate.get("candidate_id"), f"{label}.candidate_id"
        )
        if not re.fullmatch(r"candidate-[a-z0-9][a-z0-9-]{0,63}", candidate_id):
            raise ValueError(
                f"{label}.candidate_id must start with 'candidate-' and contain only "
                "lowercase letters, digits, and hyphens"
            )
        if candidate_id in candidate_ids:
            raise ValueError(f"Duplicate candidate_id: {candidate_id!r}")
        candidate_ids.add(candidate_id)
        kind = candidate.get("kind")
        if kind not in NOTE_KINDS:
            raise ValueError(f"{label}.kind must be one of {sorted(NOTE_KINDS)}")
        confidence = candidate.get("confidence")
        if confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"{label}.confidence must be one of {sorted(CONFIDENCE_LEVELS)}"
            )
        evidence = string_list(candidate.get("evidence"), f"{label}.evidence")
        if not evidence:
            raise ValueError(f"{label}.evidence must not be empty")
        normalized_candidates.append(
            {
                "candidate_id": candidate_id,
                "claim": require_string(candidate.get("claim"), f"{label}.claim"),
                "kind": kind,
                "scope": require_string(candidate.get("scope"), f"{label}.scope"),
                "applies_when": string_list(
                    candidate.get("applies_when"), f"{label}.applies_when"
                ),
                "does_not_apply_when": string_list(
                    candidate.get("does_not_apply_when"),
                    f"{label}.does_not_apply_when",
                ),
                "evidence": evidence,
                "confidence": confidence,
            }
        )

    no_knowledge_reason = bundle.get("no_knowledge_reason")
    if normalized_candidates:
        if no_knowledge_reason not in (None, ""):
            raise ValueError(
                "no_knowledge_reason must be empty when candidates are present"
            )
        normalized_reason: str | None = None
    else:
        normalized_reason = require_string(no_knowledge_reason, "no_knowledge_reason")
    return {
        "schema_version": CANDIDATE_BUNDLE_SCHEMA,
        "task_id": task_id,
        "record_digest": record_digest,
        "candidates": normalized_candidates,
        "no_knowledge_reason": normalized_reason,
    }


def candidate_bundle_digest(bundle: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(bundle))


def candidate_bundle_anchor(record: dict[str, Any]) -> tuple[str, str] | None:
    """Return the immutable candidate path/digest anchor stored in a task record."""
    raw_path = record.get("candidate_bundle")
    raw_digest = record.get("candidate_digest")
    if raw_path is None and raw_digest is None:
        return None
    if raw_path != CANDIDATE_BUNDLE_FILENAME:
        raise ValueError(
            "Task record candidate_bundle must be "
            f"{CANDIDATE_BUNDLE_FILENAME!r} once candidates are anchored"
        )
    if not isinstance(raw_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_digest):
        raise ValueError(
            "Task record candidate_digest must be a lowercase SHA-256 digest"
        )
    return raw_path, raw_digest


def load_persisted_candidate_bundle(
    task_dir: Path,
    task_id: str,
    record: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, Path]:
    anchor = candidate_bundle_anchor(record)
    if anchor is None:
        raise RuntimeError(
            f"Task {task_id} has no candidate bundle anchor in its record"
        )
    anchor_path, anchor_digest = anchor
    bundle_path = task_dir / anchor_path
    if bundle_path.is_symlink():
        raise ValueError(f"Candidate bundle cannot be a symlink: {bundle_path}")
    bundle = validate_candidate_bundle(read_json(bundle_path), task_id)
    actual_record_digest = sealed_record_digest(record, events)
    if bundle["record_digest"] != actual_record_digest:
        raise RuntimeError(
            "Persisted candidate bundle does not match the sealed task record: "
            f"expected {actual_record_digest}, found {bundle['record_digest']}; "
            "rebuild context and candidates"
        )
    actual_candidate_digest = candidate_bundle_digest(bundle)
    if actual_candidate_digest != anchor_digest:
        raise RuntimeError(
            "Persisted candidate bundle does not match the task record anchor: "
            f"expected {anchor_digest}, found {actual_candidate_digest}; "
            "do not re-save the bundle to overwrite this integrity failure"
        )
    return bundle, actual_candidate_digest, bundle_path


def deterministic_id(prefix: str, task_id: str, operation_id: str, path: str) -> str:
    token = uuid5(NAMESPACE_URL, f"{task_id}:{operation_id}:{path}").hex[:16]
    return f"{prefix}-{token}"


def build_note_metadata(
    spec: dict[str, Any],
    task_id: str,
    operation_id: str,
    relative_path: str,
    *,
    existing: dict[str, Any] | None = None,
    id_override: str | None = None,
    extra_sources: list[str] | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    protected_fields = {
        "id",
        "source_records",
        "aliases",
        "created_at",
        "updated_at",
        "revision",
        "schema_version",
    }
    unexpected_protected = sorted(protected_fields & set(spec))
    if unexpected_protected:
        raise ValueError(
            f"{operation_id}.note contains script-owned fields: {unexpected_protected}"
        )
    title = require_string(spec.get("title"), f"{operation_id}.note.title")
    summary = require_string(spec.get("summary"), f"{operation_id}.note.summary")
    kind = spec.get("kind", existing.get("kind") if existing else None)
    status = spec.get("status", existing.get("status") if existing else "provisional")
    confidence = spec.get(
        "confidence", existing.get("confidence") if existing else "medium"
    )
    scope = require_string(spec.get("scope"), f"{operation_id}.note.scope")
    if kind not in NOTE_KINDS:
        raise ValueError(
            f"{operation_id}.note.kind must be one of {sorted(NOTE_KINDS)}"
        )
    if status not in NOTE_STATUSES:
        raise ValueError(
            f"{operation_id}.note.status must be one of {sorted(NOTE_STATUSES)}"
        )
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"{operation_id}.note.confidence must be one of {sorted(CONFIDENCE_LEVELS)}"
        )

    old_sources = string_list(
        existing.get("source_records") if existing else None, "source_records"
    )
    source_records = list(
        dict.fromkeys([*old_sources, *(extra_sources or []), task_id])
    )
    old_aliases = string_list(existing.get("aliases") if existing else None, "aliases")
    merged_aliases = list(dict.fromkeys([*old_aliases, *(aliases or [])]))
    note_id = id_override or (existing.get("id") if existing else None)
    if note_id is None:
        note_id = deterministic_id("k", task_id, operation_id, relative_path)
    require_string(note_id, f"{operation_id}.note.id")

    now = utc_now()
    metadata: dict[str, Any] = {
        "schema_version": NOTE_SCHEMA,
        "id": note_id,
        "title": title,
        "summary": summary,
        "kind": kind,
        "status": status,
        "scope": scope,
        "applies_when": string_list(
            spec.get("applies_when"),
            f"{operation_id}.note.applies_when",
            string_list(
                existing.get("applies_when") if existing else None, "applies_when"
            ),
        ),
        "does_not_apply_when": string_list(
            spec.get("does_not_apply_when"),
            f"{operation_id}.note.does_not_apply_when",
            string_list(
                existing.get("does_not_apply_when") if existing else None,
                "does_not_apply_when",
            ),
        ),
        "confidence": confidence,
        "tags": string_list(
            spec.get("tags"),
            f"{operation_id}.note.tags",
            string_list(existing.get("tags") if existing else None, "tags"),
        ),
        "source_records": source_records,
        "related": string_list(
            spec.get("related"),
            f"{operation_id}.note.related",
            string_list(existing.get("related") if existing else None, "related"),
        ),
        "supersedes": string_list(
            spec.get("supersedes"),
            f"{operation_id}.note.supersedes",
            string_list(existing.get("supersedes") if existing else None, "supersedes"),
        ),
        "conflicts_with": string_list(
            spec.get("conflicts_with"),
            f"{operation_id}.note.conflicts_with",
            string_list(
                existing.get("conflicts_with") if existing else None, "conflicts_with"
            ),
        ),
        "aliases": merged_aliases,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "revision": int(existing.get("revision", 0)) + 1 if existing else 1,
    }
    return metadata


def redirect_metadata(
    task_id: str,
    operation_id: str,
    relative_path: str,
    title: str,
    target_ids: list[str],
) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": NOTE_SCHEMA,
        "id": deterministic_id("r", task_id, operation_id, relative_path),
        "title": f"Redirect: {title}",
        "summary": "This path redirects to reorganized knowledge.",
        "kind": "redirect",
        "status": "redirect",
        "scope": "Compatibility pointer retained after knowledge-tree reorganization.",
        "target_ids": target_ids,
        "source_records": [task_id],
        "aliases": [],
        "created_at": now,
        "updated_at": now,
        "revision": 1,
    }


def write_redirect(
    path: Path,
    metadata: dict[str, Any],
) -> None:
    targets = metadata["target_ids"]
    body = "## Targets\n\n" + "\n".join(f"- `{target}`" for target in targets)
    write_note(path, metadata, body)


def check_expected_hash(path: Path, expected: Any, label: str) -> None:
    expected_hash = require_string(expected, f"{label}.expected_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError(f"{label}.expected_sha256 must be a lowercase SHA-256 digest")
    actual = sha256_file(path)
    if actual != expected_hash:
        raise RuntimeError(
            f"Concurrent knowledge change detected for {path}: "
            f"expected {expected_hash}, found {actual}"
        )


def load_expected_note(
    tree: Path,
    relative_path: str,
    expected_hash: Any,
    label: str,
) -> tuple[Path, dict[str, Any], str]:
    path = note_path(tree, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge note not found: {path}")
    check_expected_hash(path, expected_hash, label)
    metadata, raw_content = parse_note(path)
    if metadata.get("kind") in {"redirect", "collection"}:
        raise ValueError(
            f"{label} must target a knowledge note, not a redirect: {relative_path}"
        )
    return path, metadata, raw_content


def body_from_spec(spec: dict[str, Any], label: str) -> str:
    return require_string(spec.get("body"), f"{label}.body")


def apply_create(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    relative_path = validate_relative_note_path(operation.get("path"))
    path = note_path(tree, relative_path)
    if path.exists():
        raise FileExistsError(f"Create target already exists: {path}")
    spec = operation.get("note")
    if not isinstance(spec, dict):
        raise ValueError(f"{operation_id}.note must be an object")
    metadata = build_note_metadata(spec, task_id, operation_id, relative_path)
    write_note(path, metadata, body_from_spec(spec, f"{operation_id}.note"))
    return [relative_path]


def apply_update(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    relative_path = validate_relative_note_path(operation.get("path"))
    path, existing, _ = load_expected_note(
        tree,
        relative_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    spec = operation.get("note")
    if not isinstance(spec, dict):
        raise ValueError(f"{operation_id}.note must be an object")
    metadata = build_note_metadata(
        spec,
        task_id,
        operation_id,
        relative_path,
        existing=existing,
    )
    write_note(path, metadata, body_from_spec(spec, f"{operation_id}.note"))
    return [relative_path]


def apply_reinforce(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    relative_path = validate_relative_note_path(operation.get("path"))
    path, metadata, raw_content = load_expected_note(
        tree,
        relative_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    confidence = operation.get("confidence", metadata.get("confidence"))
    status = operation.get("status", metadata.get("status"))
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"{operation_id}.confidence must be one of {sorted(CONFIDENCE_LEVELS)}"
        )
    if status not in NOTE_STATUSES:
        raise ValueError(
            f"{operation_id}.status must be one of {sorted(NOTE_STATUSES)}"
        )
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    current_confidence = str(metadata.get("confidence"))
    if current_confidence not in confidence_order:
        raise ValueError(
            f"Existing note {relative_path} has invalid confidence {current_confidence!r}; "
            "run doctor"
        )
    if confidence_order[confidence] < confidence_order[current_confidence]:
        raise ValueError(f"{operation_id} cannot lower confidence during reinforcement")
    current_status = str(metadata.get("status"))
    allowed_statuses = {
        "provisional": {"provisional", "verified"},
        "verified": {"verified"},
        "contested": {"contested"},
        "deprecated": {"deprecated"},
    }
    if current_status not in allowed_statuses:
        raise ValueError(
            f"Existing note {relative_path} has invalid status {current_status!r}; run doctor"
        )
    if status not in allowed_statuses[current_status]:
        raise ValueError(
            f"{operation_id} cannot change status from {current_status!r} to {status!r} "
            "during reinforcement; use update, conflict, or deprecate"
        )
    metadata["confidence"] = confidence
    metadata["status"] = status
    metadata["source_records"] = list(
        dict.fromkeys(
            [*string_list(metadata.get("source_records"), "source_records"), task_id]
        )
    )
    metadata["reinforcement_reason"] = require_string(
        operation.get("reason"), f"{operation_id}.reason"
    )
    metadata["updated_at"] = utc_now()
    metadata["revision"] = int(metadata.get("revision", 0)) + 1
    write_raw_note(path, metadata, raw_content)
    return [relative_path]


def apply_move(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    source_path = validate_relative_note_path(operation.get("source_path"))
    target_path = validate_relative_note_path(operation.get("target_path"))
    if source_path == target_path:
        raise ValueError(f"{operation_id} source_path and target_path must differ")
    source, metadata, raw_content = load_expected_note(
        tree,
        source_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    target = note_path(tree, target_path)
    if target.exists():
        raise FileExistsError(f"Move target already exists: {target}")
    metadata["updated_at"] = utc_now()
    metadata["revision"] = int(metadata.get("revision", 0)) + 1
    metadata["source_records"] = list(
        dict.fromkeys(
            [*string_list(metadata.get("source_records"), "source_records"), task_id]
        )
    )
    source.unlink()
    write_raw_note(target, metadata, raw_content)
    redirect = redirect_metadata(
        task_id,
        operation_id,
        source_path,
        str(metadata["title"]),
        [str(metadata["id"])],
    )
    write_redirect(source, redirect)
    return [source_path, target_path]


def source_specs(operation: dict[str, Any], operation_id: str) -> list[dict[str, Any]]:
    sources = operation.get("sources")
    if (
        not isinstance(sources, list)
        or len(sources) < 2
        or any(not isinstance(source, dict) for source in sources)
    ):
        raise ValueError(f"{operation_id}.sources must contain at least two objects")
    paths = [validate_relative_note_path(source.get("path")) for source in sources]
    if len(set(paths)) != len(paths):
        raise ValueError(f"{operation_id}.sources contains duplicate paths")
    return sources


def apply_merge(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    sources = source_specs(operation, operation_id)
    loaded: list[tuple[str, Path, dict[str, Any], str]] = []
    for source in sources:
        relative_path = validate_relative_note_path(source.get("path"))
        path, metadata, raw_content = load_expected_note(
            tree,
            relative_path,
            source.get("expected_sha256"),
            f"{operation_id}:{relative_path}",
        )
        loaded.append((relative_path, path, metadata, raw_content))

    target_path = validate_relative_note_path(operation.get("target_path"))
    source_paths = {item[0] for item in loaded}
    target = note_path(tree, target_path)
    if target.exists() and target_path not in source_paths:
        raise FileExistsError(f"Merge target already exists outside sources: {target}")

    source_ids = [str(item[2]["id"]) for item in loaded]
    keep_id = require_string(operation.get("keep_id"), f"{operation_id}.keep_id")
    if keep_id not in source_ids:
        raise ValueError(f"{operation_id}.keep_id must be an id from sources")
    keep_metadata = next(item[2] for item in loaded if item[2]["id"] == keep_id)
    spec = operation.get("note")
    if not isinstance(spec, dict):
        raise ValueError(f"{operation_id}.note must be an object")
    aliases: list[str] = []
    all_sources: list[str] = []
    for _, _, metadata, _ in loaded:
        all_sources.extend(
            string_list(metadata.get("source_records"), "source_records")
        )
        aliases.extend(string_list(metadata.get("aliases"), "aliases"))
        if metadata["id"] != keep_id:
            aliases.append(str(metadata["id"]))
    merged = build_note_metadata(
        spec,
        task_id,
        operation_id,
        target_path,
        existing=keep_metadata,
        id_override=keep_id,
        extra_sources=list(dict.fromkeys(all_sources)),
        aliases=list(dict.fromkeys(aliases)),
    )
    created_times = [str(item[2].get("created_at", utc_now())) for item in loaded]
    merged["created_at"] = min(created_times)
    merged["revision"] = max(int(item[2].get("revision", 0)) for item in loaded) + 1

    for _, path, _, _ in loaded:
        path.unlink()
    write_note(target, merged, body_from_spec(spec, f"{operation_id}.note"))
    for relative_path, path, metadata, _ in loaded:
        if relative_path == target_path:
            continue
        redirect = redirect_metadata(
            task_id,
            operation_id,
            relative_path,
            str(metadata["title"]),
            [keep_id],
        )
        write_redirect(path, redirect)
    return sorted({*source_paths, target_path})


def apply_split(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    source_path = validate_relative_note_path(operation.get("source_path"))
    source, source_metadata, _ = load_expected_note(
        tree,
        source_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    targets = operation.get("targets")
    if (
        not isinstance(targets, list)
        or len(targets) < 2
        or any(not isinstance(target, dict) for target in targets)
    ):
        raise ValueError(f"{operation_id}.targets must contain at least two objects")
    target_paths = [
        validate_relative_note_path(target.get("path")) for target in targets
    ]
    if len(set(target_paths)) != len(target_paths):
        raise ValueError(f"{operation_id}.targets contains duplicate paths")
    inherited = [
        target for target in targets if target.get("inherit_source_id") is True
    ]
    if len(inherited) != 1:
        raise ValueError(
            f"{operation_id} must have exactly one target with inherit_source_id=true"
        )
    for index, target in enumerate(targets):
        if (
            target_paths[index] == source_path
            and target.get("inherit_source_id") is not True
        ):
            raise ValueError(
                f"{operation_id} must preserve the source id when reusing source_path as a target"
            )
    for relative_path in target_paths:
        path = note_path(tree, relative_path)
        if path.exists() and relative_path != source_path:
            raise FileExistsError(f"Split target already exists: {path}")

    source.unlink()
    target_ids: list[str] = []
    for index, target_spec in enumerate(targets):
        relative_path = target_paths[index]
        spec = target_spec.get("note")
        if not isinstance(spec, dict):
            raise ValueError(f"{operation_id}.targets[{index}].note must be an object")
        inherits = target_spec.get("inherit_source_id") is True
        metadata = build_note_metadata(
            spec,
            task_id,
            f"{operation_id}-target-{index + 1}",
            relative_path,
            existing=source_metadata if inherits else None,
            id_override=str(source_metadata["id"]) if inherits else None,
            extra_sources=string_list(
                source_metadata.get("source_records"), "source_records"
            ),
            aliases=(
                string_list(source_metadata.get("aliases"), "aliases")
                if inherits
                else None
            ),
        )
        target_ids.append(str(metadata["id"]))
        write_note(
            note_path(tree, relative_path),
            metadata,
            body_from_spec(spec, f"{operation_id}.targets[{index}].note"),
        )

    if source_path not in target_paths:
        redirect = redirect_metadata(
            task_id,
            operation_id,
            source_path,
            str(source_metadata["title"]),
            target_ids,
        )
        write_redirect(source, redirect)
    return sorted({source_path, *target_paths})


def apply_deprecate(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    relative_path = validate_relative_note_path(operation.get("path"))
    path, metadata, raw_content = load_expected_note(
        tree,
        relative_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    metadata["status"] = "deprecated"
    metadata["deprecation_reason"] = require_string(
        operation.get("reason"), f"{operation_id}.reason"
    )
    metadata["source_records"] = list(
        dict.fromkeys(
            [*string_list(metadata.get("source_records"), "source_records"), task_id]
        )
    )
    metadata["updated_at"] = utc_now()
    metadata["revision"] = int(metadata.get("revision", 0)) + 1
    write_raw_note(path, metadata, raw_content)
    return [relative_path]


def apply_conflict(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_id = operation["operation_id"]
    existing_path = validate_relative_note_path(operation.get("existing_path"))
    existing_file, existing, existing_content = load_expected_note(
        tree,
        existing_path,
        operation.get("expected_sha256"),
        operation_id,
    )
    candidate_path = validate_relative_note_path(operation.get("candidate_path"))
    candidate_file = note_path(tree, candidate_path)
    if candidate_file.exists():
        raise FileExistsError(
            f"Conflict candidate target already exists: {candidate_file}"
        )
    spec = operation.get("candidate_note")
    if not isinstance(spec, dict):
        raise ValueError(f"{operation_id}.candidate_note must be an object")
    spec = dict(spec)
    spec["status"] = "contested"
    candidate = build_note_metadata(spec, task_id, operation_id, candidate_path)
    candidate["conflicts_with"] = list(
        dict.fromkeys(
            [
                *string_list(candidate.get("conflicts_with"), "conflicts_with"),
                str(existing["id"]),
            ]
        )
    )
    existing["status"] = "contested"
    existing["conflicts_with"] = list(
        dict.fromkeys(
            [
                *string_list(existing.get("conflicts_with"), "conflicts_with"),
                str(candidate["id"]),
            ]
        )
    )
    existing["source_records"] = list(
        dict.fromkeys(
            [*string_list(existing.get("source_records"), "source_records"), task_id]
        )
    )
    existing["updated_at"] = utc_now()
    existing["revision"] = int(existing.get("revision", 0)) + 1
    write_raw_note(existing_file, existing, existing_content)
    write_note(
        candidate_file,
        candidate,
        body_from_spec(spec, f"{operation_id}.candidate_note"),
    )
    return [existing_path, candidate_path]


def apply_operation(tree: Path, operation: dict[str, Any], task_id: str) -> list[str]:
    operation_type = operation["op"]
    handlers = {
        "create": apply_create,
        "reinforce": apply_reinforce,
        "update": apply_update,
        "merge": apply_merge,
        "split": apply_split,
        "move": apply_move,
        "deprecate": apply_deprecate,
        "conflict": apply_conflict,
    }
    return handlers[operation_type](tree, operation, task_id)


def validate_plan(plan: dict[str, Any], task_id: str) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError(f"Plan schema must be {PLAN_SCHEMA!r}")
    if plan.get("task_id") != task_id:
        raise ValueError(f"Plan task_id must equal {task_id!r}")
    require_string(plan.get("plan_id"), "plan_id")
    expected_tree_digest = require_string(
        plan.get("expected_tree_digest"), "expected_tree_digest"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_tree_digest):
        raise ValueError("expected_tree_digest must be a lowercase SHA-256 digest")
    expected_record_digest = require_string(
        plan.get("expected_record_digest"), "expected_record_digest"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_record_digest):
        raise ValueError("expected_record_digest must be a lowercase SHA-256 digest")
    expected_candidate_digest = require_string(
        plan.get("expected_candidate_digest"), "expected_candidate_digest"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_candidate_digest):
        raise ValueError("expected_candidate_digest must be a lowercase SHA-256 digest")
    review = plan.get("tree_review")
    if not isinstance(review, dict):
        raise ValueError("tree_review must be an object")
    inspected = string_list(
        review.get("inspected_paths"), "tree_review.inspected_paths"
    )
    if not inspected:
        raise ValueError("tree_review.inspected_paths must not be empty")
    require_string(review.get("decision"), "tree_review.decision")
    operations = plan.get("operations")
    if not isinstance(operations, list) or any(
        not isinstance(item, dict) for item in operations
    ):
        raise ValueError("operations must be a list of objects")
    operation_ids: set[str] = set()
    for index, operation in enumerate(operations):
        operation_id = require_string(
            operation.get("operation_id"), f"operations[{index}].operation_id"
        )
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", operation_id):
            raise ValueError(f"Invalid operation_id: {operation_id!r}")
        if operation_id in operation_ids:
            raise ValueError(f"Duplicate operation_id: {operation_id!r}")
        operation_ids.add(operation_id)
        operation_type = operation.get("op")
        if operation_type not in OPERATION_TYPES:
            raise ValueError(
                f"operations[{index}].op must be one of {sorted(OPERATION_TYPES)}"
            )
        require_string(operation.get("reason"), f"operations[{index}].reason")
    bindings = plan.get("candidate_bindings")
    if not isinstance(bindings, list) or any(
        not isinstance(item, dict) for item in bindings
    ):
        raise ValueError("candidate_bindings must be a list of objects")
    bound_candidate_ids: set[str] = set()
    for index, binding in enumerate(bindings):
        label = f"candidate_bindings[{index}]"
        candidate_id = require_string(
            binding.get("candidate_id"), f"{label}.candidate_id"
        )
        if not re.fullmatch(r"candidate-[a-z0-9][a-z0-9-]{0,63}", candidate_id):
            raise ValueError(f"Invalid candidate_id in {label}: {candidate_id!r}")
        if candidate_id in bound_candidate_ids:
            raise ValueError(
                f"Candidate {candidate_id!r} has more than one disposition"
            )
        bound_candidate_ids.add(candidate_id)
        disposition = binding.get("disposition")
        if disposition not in CANDIDATE_DISPOSITIONS:
            raise ValueError(
                f"{label}.disposition must be one of {sorted(CANDIDATE_DISPOSITIONS)}"
            )
        raw_operation_ids = binding.get("operation_ids", [])
        if not isinstance(raw_operation_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_operation_ids
        ):
            raise ValueError(
                f"{label}.operation_ids must be a list of non-empty strings"
            )
        binding_operation_ids = [item.strip() for item in raw_operation_ids]
        if len(binding_operation_ids) != len(set(binding_operation_ids)):
            raise ValueError(f"{label}.operation_ids must not contain duplicates")
        unknown_operation_ids = sorted(set(binding_operation_ids) - operation_ids)
        if unknown_operation_ids:
            raise ValueError(
                f"{label}.operation_ids reference unknown operations: {unknown_operation_ids}"
            )
        reason = binding.get("reason")
        if disposition == "applied":
            if not binding_operation_ids:
                raise ValueError(
                    f"{label}.operation_ids must not be empty for disposition 'applied'"
                )
            if reason not in (None, ""):
                require_string(reason, f"{label}.reason")
        else:
            if binding_operation_ids:
                raise ValueError(
                    f"{label}.operation_ids must be empty for disposition {disposition!r}"
                )
            require_string(reason, f"{label}.reason")
    no_knowledge_reason = plan.get("no_knowledge_reason")
    if not operations:
        require_string(no_knowledge_reason, "no_knowledge_reason")
    elif no_knowledge_reason not in (None, ""):
        raise ValueError(
            "no_knowledge_reason must be empty when operations are present"
        )


def validate_plan_candidate_binding(
    plan: dict[str, Any], bundle: dict[str, Any], actual_candidate_digest: str
) -> None:
    expected_candidate_digest = plan["expected_candidate_digest"]
    if actual_candidate_digest != expected_candidate_digest:
        raise RuntimeError(
            "Candidate bundle changed after curation handoff: "
            f"expected {expected_candidate_digest}, found {actual_candidate_digest}; "
            "reread the persisted bundle and rebase the plan"
        )
    candidate_ids = {item["candidate_id"] for item in bundle["candidates"]}
    bound_candidate_ids = {
        binding["candidate_id"] for binding in plan["candidate_bindings"]
    }
    missing = sorted(candidate_ids - bound_candidate_ids)
    unknown = sorted(bound_candidate_ids - candidate_ids)
    if missing or unknown:
        raise ValueError(
            "candidate_bindings must give every persisted candidate exactly one disposition; "
            f"missing={missing}, unknown={unknown}"
        )


def validate_review_paths(root: Path, plan: dict[str, Any]) -> None:
    inspected = plan["tree_review"]["inspected_paths"]
    for raw_path in inspected:
        if "\\" in raw_path:
            raise ValueError(f"tree_review path must use '/' separators: {raw_path!r}")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise ValueError(
                f"tree_review path escapes the knowledge tree: {raw_path!r}"
            )
        path = (root / "knowledge").joinpath(*pure.parts)
        if not path.exists():
            raise FileNotFoundError(f"Inspected knowledge path does not exist: {path}")


def validate_expected_tree_digest(
    plan: dict[str, Any], catalog: dict[str, Any]
) -> None:
    expected = plan["expected_tree_digest"]
    actual = catalog.get("tree_digest")
    if actual != expected:
        raise RuntimeError(
            f"Knowledge tree changed after review: expected {expected}, found {actual}; "
            "rebuild context and rebase the plan"
        )


def validate_expected_record_digest(
    plan: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    expected = plan["expected_record_digest"]
    actual = sealed_record_digest(record, events)
    if actual != expected:
        raise RuntimeError(
            f"Sealed task record changed after context generation: expected {expected}, "
            f"found {actual}; rebuild context and rebase the plan"
        )


def scan_note_files(tree: Path) -> list[Path]:
    if not tree.exists():
        return []
    assert_no_symlinks(tree)
    return sorted(
        path
        for path in tree.rglob("*.md")
        if path.is_file() and path.name != "_index.md"
    )


def assert_no_symlinks(tree: Path) -> None:
    if tree.is_symlink():
        raise ValueError(f"The knowledge tree root cannot be a symlink: {tree}")
    for path in tree.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed in the knowledge tree: {path}")


def rebuild_indexes(tree: Path) -> None:
    tree.mkdir(parents=True, exist_ok=True)
    assert_no_symlinks(tree)
    for index_path in list(tree.rglob("_index.md")):
        index_path.unlink()

    for directory in sorted(
        (path for path in tree.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()

    note_files = scan_note_files(tree)
    directories: set[Path] = {tree}
    for note_file in note_files:
        current = note_file.parent
        while current == tree or tree in current.parents:
            directories.add(current)
            if current == tree:
                break
            current = current.parent

    for directory in sorted(directories):
        relative_dir = directory.relative_to(tree).as_posix()
        heading = (
            "Knowledge index"
            if relative_dir == "."
            else f"Knowledge index: {relative_dir}"
        )
        child_dirs = sorted(path for path in directory.iterdir() if path.is_dir())
        direct_notes = sorted(
            path for path in directory.glob("*.md") if path.name != "_index.md"
        )
        lines = [
            f"# {heading}",
            "",
            "Generated by `knowledge_store.py`; do not edit manually.",
            "",
        ]
        if child_dirs:
            lines.extend(["## Directories", ""])
            for child in child_dirs:
                descendant_count = len(
                    [path for path in child.rglob("*.md") if path.name != "_index.md"]
                )
                lines.append(
                    f"- [{child.name}/]({child.name}/_index.md) — {descendant_count} item(s)"
                )
            lines.append("")
        if direct_notes:
            lines.extend(["## Notes", ""])
            for path in direct_notes:
                metadata, _ = parse_note(path)
                summary = str(metadata.get("summary", "")).replace("\n", " ").strip()
                lines.append(f"- [{metadata['title']}]({path.name}) — {summary}")
            lines.append("")
        if not child_dirs and not direct_notes:
            lines.extend(["No knowledge has been distilled yet.", ""])
        atomic_write_text(directory / "_index.md", "\n".join(lines))


def validate_and_catalog(root: Path, tree: Path) -> dict[str, Any]:
    note_files = scan_note_files(tree)
    records = all_record_ids(root)
    notes: list[dict[str, Any]] = []
    id_to_path: dict[str, str] = {}
    real_ids: set[str] = set()
    alias_to_id: dict[str, str] = {}
    redirects: dict[str, list[str]] = {}
    parsed: list[tuple[Path, dict[str, Any]]] = []

    for path in note_files:
        metadata, _ = parse_note(path)
        relative_path = path.relative_to(tree).as_posix()
        note_id = require_string(metadata.get("id"), f"{relative_path}.id")
        if note_id in id_to_path:
            raise ValueError(
                f"Duplicate knowledge id {note_id!r}: {id_to_path[note_id]} and {relative_path}"
            )
        id_to_path[note_id] = relative_path
        parsed.append((path, metadata))

    for path, metadata in parsed:
        relative_path = path.relative_to(tree).as_posix()
        note_id = str(metadata["id"])
        kind = metadata.get("kind")
        status = metadata.get("status")
        if kind in {"redirect", "collection"}:
            if status != "redirect":
                raise ValueError(f"Redirect must have status=redirect: {relative_path}")
            target_ids = string_list(
                metadata.get("target_ids"), f"{relative_path}.target_ids"
            )
            if not target_ids:
                raise ValueError(f"Redirect has no targets: {relative_path}")
            redirects[relative_path] = target_ids
        else:
            real_ids.add(note_id)
            if kind not in NOTE_KINDS:
                raise ValueError(f"Invalid note kind {kind!r}: {relative_path}")
            if status not in NOTE_STATUSES:
                raise ValueError(f"Invalid note status {status!r}: {relative_path}")
            if metadata.get("confidence") not in CONFIDENCE_LEVELS:
                raise ValueError(f"Invalid note confidence: {relative_path}")
            require_string(metadata.get("title"), f"{relative_path}.title")
            require_string(metadata.get("summary"), f"{relative_path}.summary")
            require_string(metadata.get("scope"), f"{relative_path}.scope")
            for alias in string_list(
                metadata.get("aliases"), f"{relative_path}.aliases"
            ):
                if alias in id_to_path:
                    raise ValueError(
                        f"Alias {alias!r} collides with a live id in {relative_path}"
                    )
                previous = alias_to_id.get(alias)
                if previous and previous != note_id:
                    raise ValueError(
                        f"Alias {alias!r} points to both {previous!r} and {note_id!r}"
                    )
                alias_to_id[alias] = note_id

        source_records = string_list(
            metadata.get("source_records"), f"{relative_path}.source_records"
        )
        missing_records = sorted(set(source_records) - records)
        if missing_records:
            raise ValueError(
                f"Knowledge note {relative_path} references missing records: {missing_records}"
            )

        notes.append(
            {
                "id": note_id,
                "path": relative_path,
                "title": metadata.get("title"),
                "summary": metadata.get("summary"),
                "kind": kind,
                "status": status,
                "scope": metadata.get("scope"),
                "confidence": metadata.get("confidence"),
                "tags": metadata.get("tags", []),
                "source_records": metadata.get("source_records", []),
                "revision": metadata.get("revision"),
                "sha256": sha256_file(path),
            }
        )

    resolvable = real_ids | set(alias_to_id)
    for relative_path, target_ids in redirects.items():
        missing = sorted(set(target_ids) - resolvable)
        if missing:
            raise ValueError(
                f"Redirect {relative_path} has unresolved targets: {missing}"
            )
    for path, metadata in parsed:
        if metadata.get("kind") in {"redirect", "collection"}:
            continue
        for field in ("related", "supersedes", "conflicts_with"):
            missing = sorted(set(string_list(metadata.get(field), field)) - resolvable)
            if missing:
                relative_path = path.relative_to(tree).as_posix()
                raise ValueError(
                    f"{relative_path}.{field} has unresolved ids: {missing}"
                )

    sorted_notes = sorted(notes, key=lambda item: str(item["path"]))
    tree_digest = sha256_bytes(
        canonical_json(
            [[str(item["path"]), str(item["sha256"])] for item in sorted_notes]
        )
    )
    return {
        "schema_version": CATALOG_SCHEMA,
        "generated_at": utc_now(),
        "knowledge_root": str(root),
        "tree_digest": tree_digest,
        "notes": sorted_notes,
        "aliases": dict(sorted(alias_to_id.items())),
        "redirects": dict(sorted(redirects.items())),
    }


def validate_derived_store(root: Path) -> dict[str, Any]:
    """Validate the canonical tree and every derived catalog/index artifact."""
    tree = root / "knowledge"
    computed_catalog = validate_and_catalog(root, tree)
    stored_catalog = read_json(root / ".meta" / "catalog.json")
    expected_catalog = dict(computed_catalog)
    actual_catalog = dict(stored_catalog)
    expected_catalog.pop("generated_at", None)
    actual_catalog.pop("generated_at", None)
    if expected_catalog != actual_catalog:
        raise RuntimeError("Catalog is stale or corrupted; run doctor --repair")

    assert_no_symlinks(tree)
    with tempfile.TemporaryDirectory(
        prefix="distill-task-knowledge-doctor-"
    ) as temporary:
        expected_tree = Path(temporary) / "knowledge"
        shutil.copytree(tree, expected_tree, symlinks=True)
        assert_no_symlinks(expected_tree)
        rebuild_indexes(expected_tree)
        expected_indexes = {
            path.relative_to(expected_tree).as_posix(): path.read_text(encoding="utf-8")
            for path in expected_tree.rglob("_index.md")
        }
    actual_indexes = {
        path.relative_to(tree).as_posix(): path.read_text(encoding="utf-8")
        for path in tree.rglob("_index.md")
    }
    if expected_indexes != actual_indexes:
        raise RuntimeError("Generated knowledge indexes are stale; run doctor --repair")
    return computed_catalog


def derived_store_health(root: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        catalog = validate_derived_store(root)
    except Exception as exc:
        return (
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            None,
        )
    return (
        {
            "ok": True,
            "tree_digest": catalog["tree_digest"],
            "knowledge_items": len(catalog.get("notes", [])),
        },
        catalog,
    )


def lock_owner(lock_dir: Path) -> dict[str, Any]:
    if lock_dir.is_symlink():
        raise ValueError(f"Lock path cannot be a symlink: {lock_dir}")
    owner_path = lock_dir / "owner.json"
    return (
        read_json(owner_path)
        if owner_path.is_file()
        else {"detail": "owner metadata missing"}
    )


@contextmanager
def lock_transition(lock_dir: Path) -> Iterator[None]:
    transition_path = lock_dir.with_name(f".{lock_dir.name}.transition")
    flags = (
        os.O_CREAT
        | os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(transition_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(
                f"Lock transition path must be a regular file: {transition_path}"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def directory_lock(
    lock_dir: Path,
    purpose: str,
    *,
    wait_seconds: float,
    clear_local_stale: bool,
) -> Iterator[str]:
    token = uuid4().hex
    deadline = time.monotonic() + wait_seconds
    owner_metadata = {
        "token": token,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "purpose": purpose,
        "created_at": utc_now(),
    }
    while True:
        acquired = False
        reclaimed = False
        owner: dict[str, Any] = {"detail": "lock state changed"}
        with lock_transition(lock_dir):
            try:
                lock_dir.mkdir()
            except FileExistsError:
                owner = lock_owner(lock_dir)
                owner_pid = owner.get("pid")
                owner_token = owner.get("token")
                if (
                    clear_local_stale
                    and owner.get("hostname") == socket.gethostname()
                    and isinstance(owner_pid, int)
                    and isinstance(owner_token, str)
                    and not process_is_alive(owner_pid)
                    and lock_owner(lock_dir) == owner
                ):
                    shutil.rmtree(lock_dir)
                    fsync_directory(lock_dir.parent)
                    reclaimed = True
            else:
                try:
                    atomic_write_json(lock_dir / "owner.json", owner_metadata)
                except BaseException:
                    shutil.rmtree(lock_dir, ignore_errors=True)
                    raise
                acquired = True
        if acquired:
            break
        if reclaimed:
            continue
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Lock is held at {lock_dir}: {owner}") from None
        time.sleep(0.05)

    try:
        yield token
    finally:
        with lock_transition(lock_dir):
            try:
                owner = lock_owner(lock_dir)
            except (FileNotFoundError, ValueError):
                owner = {}
            if owner.get("token") == token:
                shutil.rmtree(lock_dir, ignore_errors=True)
                fsync_directory(lock_dir.parent)


@contextmanager
def write_lock(root: Path, purpose: str) -> Iterator[str]:
    lock_dir = root / ".meta" / "locks" / "knowledge-write"
    with directory_lock(
        lock_dir,
        purpose,
        wait_seconds=0.0,
        clear_local_stale=False,
    ) as token:
        yield token


@contextmanager
def task_lock(root: Path, task_id: str, purpose: str) -> Iterator[str]:
    safe_task_id = validate_task_id(task_id)
    lock_dir = root / ".meta" / "locks" / f"record-{safe_task_id}"
    with directory_lock(
        lock_dir,
        purpose,
        wait_seconds=10.0,
        clear_local_stale=True,
    ) as token:
        yield token


def assert_write_lock_owned(root: Path, token: str) -> None:
    lock_dir = root / ".meta" / "locks" / "knowledge-write"
    owner = lock_owner(lock_dir)
    if owner.get("token") != token:
        raise RuntimeError("Knowledge write lock ownership changed; aborting mutation")


def transaction_status_path(transaction_dir: Path) -> Path:
    return transaction_dir / "status.json"


def assert_transaction_components_safe(root: Path, transaction_dir: Path) -> None:
    transactions_root = root / ".meta" / "transactions"
    if transaction_dir.parent != transactions_root or transaction_dir.is_symlink():
        raise ValueError(f"Transaction directory escapes the store: {transaction_dir}")
    component_paths = (
        transaction_dir / "stage",
        transaction_dir / "stage" / "knowledge",
        transaction_dir / "before",
        transaction_dir / "before" / "knowledge",
        transaction_dir / "before" / "catalog.json",
        transaction_dir / "failed",
        transaction_dir / "failed" / "knowledge",
    )
    for path in component_paths:
        if path.is_symlink():
            raise ValueError(f"Transaction component cannot be a symlink: {path}")


def validate_transaction_status(
    root: Path,
    transaction_dir: Path,
    status: dict[str, Any],
) -> tuple[str, str, str, str]:
    transactions_root = root / ".meta" / "transactions"
    if transaction_dir.is_symlink() or transaction_dir.parent != transactions_root:
        raise ValueError(f"Transaction directory escapes the store: {transaction_dir}")
    transaction_id = validate_transaction_id(
        require_string(status.get("transaction_id"), "transaction_id")
    )
    if transaction_dir.name != transaction_id:
        raise ValueError(
            f"Transaction id {transaction_id!r} does not match directory {transaction_dir.name!r}"
        )
    if status.get("schema_version") != TRANSACTION_SCHEMA:
        raise ValueError(f"Unsupported transaction schema in {transaction_dir}")
    task_id = validate_task_id(require_string(status.get("task_id"), "task_id"))
    plan_hash = require_string(status.get("plan_hash"), "plan_hash")
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
        raise ValueError(f"Invalid transaction plan hash in {transaction_dir}")
    state = require_string(status.get("status"), "status")
    allowed_states = {
        "planned",
        "prepared",
        "committing",
        "committed",
        "rolled-back",
        "recovery-required",
    }
    if state not in allowed_states:
        raise ValueError(f"Invalid transaction state {state!r} in {transaction_dir}")
    return transaction_id, task_id, plan_hash, state


def write_transaction_status(
    transaction_dir: Path,
    transaction_id: str,
    task_id: str,
    plan_hash: str,
    status: str,
) -> None:
    validate_transaction_id(transaction_id)
    validate_task_id(task_id)
    if transaction_dir.name != transaction_id or transaction_dir.is_symlink():
        raise ValueError(f"Invalid transaction directory: {transaction_dir}")
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
        raise ValueError(f"Invalid transaction plan hash: {plan_hash!r}")
    if status not in {
        "planned",
        "prepared",
        "committing",
        "committed",
        "rolled-back",
        "recovery-required",
    }:
        raise ValueError(f"Invalid transaction status: {status!r}")
    atomic_write_json(
        transaction_status_path(transaction_dir),
        {
            "schema_version": TRANSACTION_SCHEMA,
            "transaction_id": transaction_id,
            "task_id": task_id,
            "plan_hash": plan_hash,
            "status": status,
            "updated_at": utc_now(),
        },
    )


def commit_staged_tree(
    root: Path,
    transaction_dir: Path,
    transaction_id: str,
    task_id: str,
    plan_hash: str,
    catalog: dict[str, Any],
    lock_token: str,
) -> None:
    live_tree = root / "knowledge"
    staged_tree = transaction_dir / "stage" / "knowledge"
    backup_tree = transaction_dir / "before" / "knowledge"
    catalog_path = root / ".meta" / "catalog.json"
    backup_catalog = transaction_dir / "before" / "catalog.json"
    failed_tree = transaction_dir / "failed" / "knowledge"
    assert_transaction_components_safe(root, transaction_dir)
    mkdir_chain_without_symlinks(transaction_dir, ["before"])
    assert_transaction_components_safe(root, transaction_dir)
    shutil.copy2(catalog_path, backup_catalog)
    with backup_catalog.open("rb") as handle:
        os.fsync(handle.fileno())
    fsync_directory(backup_catalog.parent)
    assert_write_lock_owned(root, lock_token)
    write_transaction_status(
        transaction_dir, transaction_id, task_id, plan_hash, "committing"
    )
    try:
        assert_write_lock_owned(root, lock_token)
        os.replace(live_tree, backup_tree)
        fsync_directory(root)
        fsync_directory(backup_tree.parent)
        assert_write_lock_owned(root, lock_token)
        os.replace(staged_tree, live_tree)
        fsync_directory(root)
        fsync_directory(staged_tree.parent)
        assert_write_lock_owned(root, lock_token)
        atomic_write_json(catalog_path, catalog)
        assert_write_lock_owned(root, lock_token)
        write_transaction_status(
            transaction_dir, transaction_id, task_id, plan_hash, "committed"
        )
    except BaseException as commit_error:
        try:
            assert_transaction_components_safe(root, transaction_dir)
            if backup_tree.exists():
                if live_tree.exists():
                    mkdir_chain_without_symlinks(transaction_dir, ["failed"])
                    assert_transaction_components_safe(root, transaction_dir)
                    if failed_tree.exists():
                        shutil.rmtree(failed_tree)
                    os.replace(live_tree, failed_tree)
                os.replace(backup_tree, live_tree)
                fsync_directory(root)
            if backup_catalog.exists():
                atomic_write_text(
                    catalog_path,
                    backup_catalog.read_text(encoding="utf-8"),
                )
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
            )
        except BaseException as recovery_error:
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "recovery-required"
            )
            raise RuntimeError(
                f"Commit failed and automatic rollback also failed: {recovery_error}"
            ) from commit_error
        raise
    shutil.rmtree(transaction_dir / "before", ignore_errors=True)


def iter_all_task_records(root: Path) -> Iterator[tuple[Path, Path, dict[str, Any]]]:
    """Yield active and archived task records without following symlinks."""
    records_root = root / "records"
    if records_root.is_symlink():
        raise ValueError(f"Records directory cannot be a symlink: {records_root}")
    for record_path in sorted(records_root.rglob("task-record.json")):
        relative = record_path.relative_to(records_root)
        current = records_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"Task record component cannot be a symlink: {current}"
                )
        task_dir = record_path.parent
        events_dir = task_dir / "events"
        if events_dir.is_symlink():
            raise ValueError(f"Task events directory cannot be a symlink: {events_dir}")
        record = read_json(record_path)
        if record.get("schema_version") != RECORD_SCHEMA:
            raise ValueError(f"Unsupported task record schema in {record_path}")
        task_id = validate_task_id(require_string(record.get("task_id"), "task_id"))
        if task_dir.name != task_id:
            raise ValueError(
                f"Task record id does not match its directory: {record_path}"
            )
        yield task_dir, record_path, record


def find_task_record(root: Path, task_id: str) -> tuple[Path, Path, dict[str, Any]]:
    validate_task_id(task_id)
    active_path = active_task_dir(root, task_id)
    if active_path.is_symlink():
        raise ValueError(f"Task record directory cannot be a symlink: {active_path}")
    matches = [
        item
        for item in iter_all_task_records(root)
        if item[2].get("task_id") == task_id
    ]
    if not matches:
        raise FileNotFoundError(f"Task record not found for transaction task {task_id}")
    if len(matches) != 1:
        raise ValueError(f"Multiple task records found for task {task_id}")
    return matches[0]


def planned_result_touched_paths(plan: dict[str, Any]) -> list[str]:
    """Derive the exact touched-path summary from a validated plan."""
    touched: set[str] = set()
    for operation in plan["operations"]:
        operation_type = operation["op"]
        if operation_type in {"create", "reinforce", "update", "deprecate"}:
            touched.add(validate_relative_note_path(operation.get("path")))
        elif operation_type == "move":
            touched.add(validate_relative_note_path(operation.get("source_path")))
            touched.add(validate_relative_note_path(operation.get("target_path")))
        elif operation_type == "merge":
            operation_id = require_string(operation.get("operation_id"), "operation_id")
            for source in source_specs(operation, operation_id):
                touched.add(validate_relative_note_path(source.get("path")))
            touched.add(validate_relative_note_path(operation.get("target_path")))
        elif operation_type == "split":
            touched.add(validate_relative_note_path(operation.get("source_path")))
            targets = operation.get("targets")
            if not isinstance(targets, list) or any(
                not isinstance(target, dict) for target in targets
            ):
                raise ValueError("split targets must be a list of objects")
            for target in targets:
                touched.add(validate_relative_note_path(target.get("path")))
        elif operation_type == "conflict":
            touched.add(validate_relative_note_path(operation.get("existing_path")))
            touched.add(validate_relative_note_path(operation.get("candidate_path")))
        else:  # validate_plan rejects this before the helper is called.
            raise ValueError(
                f"Unsupported operation type in result: {operation_type!r}"
            )
    return sorted(touched)


def validate_result_plan_semantics(
    root: Path,
    plan: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Bind every result summary field to its deterministic plan/store source."""
    expected_fields = {
        "schema_version",
        "ok",
        "task_id",
        "plan_id",
        "plan_hash",
        "candidate_digest",
        "transaction_id",
        "distillation_status",
        "operation_count",
        "touched_paths",
        "no_knowledge_reason",
        "knowledge_root",
        "catalog_path",
        "committed_at",
    }
    missing = sorted(expected_fields - set(result))
    unexpected = sorted(set(result) - expected_fields)
    if missing or unexpected:
        raise ValueError(
            f"Distillation result fields do not match the schema: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if result.get("ok") is not True:
        raise ValueError("Distillation result ok must be true")

    operations = plan["operations"]
    expected_status = "committed" if operations else "no-knowledge"
    if result.get("distillation_status") != expected_status:
        raise ValueError(
            "Distillation result status does not match whether the plan has operations"
        )
    operation_count = result.get("operation_count")
    if type(operation_count) is not int or operation_count != len(operations):
        raise ValueError("Distillation result operation_count does not match the plan")
    if result.get("touched_paths") != planned_result_touched_paths(plan):
        raise ValueError("Distillation result touched_paths do not match the plan")
    if result.get("no_knowledge_reason") != plan.get("no_knowledge_reason"):
        raise ValueError(
            "Distillation result no_knowledge_reason does not match the plan"
        )
    if result.get("knowledge_root") != str(root):
        raise ValueError("Distillation result knowledge_root does not match the store")
    if result.get("catalog_path") != str(root / ".meta" / "catalog.json"):
        raise ValueError("Distillation result catalog_path does not match the store")
    require_string(result.get("committed_at"), "result.committed_at")


def validate_result_candidate_chain(
    root: Path,
    task_dir: Path,
    record: dict[str, Any],
    result: dict[str, Any],
    *,
    allowed_transaction_states: frozenset[str] = frozenset({"committed"}),
) -> None:
    """Validate result -> transaction plan -> candidate anchor as one audit chain."""
    task_id = validate_task_id(require_string(record.get("task_id"), "task_id"))
    if result.get("schema_version") != RESULT_SCHEMA:
        raise ValueError("Unsupported distillation result schema")
    if result.get("task_id") != task_id:
        raise ValueError("Distillation result task_id does not match the record")
    if result.get("distillation_status") not in {"committed", "no-knowledge"}:
        raise ValueError("Distillation result has an invalid terminal status")

    result_plan_hash = require_string(result.get("plan_hash"), "result.plan_hash")
    if not re.fullmatch(r"[0-9a-f]{64}", result_plan_hash):
        raise ValueError("Distillation result has an invalid plan hash")
    result_candidate_digest = require_string(
        result.get("candidate_digest"), "result.candidate_digest"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", result_candidate_digest):
        raise ValueError("Distillation result has an invalid candidate digest")
    transaction_id = validate_transaction_id(
        require_string(result.get("transaction_id"), "result.transaction_id")
    )
    transaction_dir = root / ".meta" / "transactions" / transaction_id
    assert_transaction_components_safe(root, transaction_dir)

    status_path = transaction_status_path(transaction_dir)
    plan_path = transaction_dir / "plan.json"
    transaction_result_path = transaction_dir / "result.json"
    task_plan_path = task_dir / "distillation-plan.json"
    for path in (status_path, plan_path, transaction_result_path, task_plan_path):
        if path.is_symlink():
            raise ValueError(
                f"Candidate audit-chain component cannot be a symlink: {path}"
            )

    status = read_json(status_path)
    stored_id, stored_task, stored_plan_hash, stored_state = (
        validate_transaction_status(root, transaction_dir, status)
    )
    if stored_id != transaction_id or stored_task != task_id:
        raise ValueError("Distillation result does not identify its transaction task")
    if stored_state not in allowed_transaction_states:
        raise ValueError(
            f"Distillation transaction {transaction_id} is {stored_state!r}, expected one of "
            f"{sorted(allowed_transaction_states)}"
        )

    plan = read_json(plan_path)
    validate_plan(plan, task_id)
    canonical_plan_hash = sha256_bytes(canonical_json(plan))
    if (
        canonical_plan_hash != stored_plan_hash
        or canonical_plan_hash != result_plan_hash
    ):
        raise ValueError(
            "Transaction plan canonical hash does not match status/result: "
            f"canonical={canonical_plan_hash}, status={stored_plan_hash}, "
            f"result={result_plan_hash}"
        )
    expected_transaction_id = distillation_transaction_id(task_id, canonical_plan_hash)
    if transaction_id != expected_transaction_id:
        raise ValueError(
            "Distillation result transaction_id does not match its task and plan hash"
        )
    task_plan = read_json(task_plan_path)
    if (
        sha256_bytes(canonical_json(task_plan)) != canonical_plan_hash
        or task_plan != plan
    ):
        raise ValueError("Task and transaction distillation plans do not match")
    if result.get("plan_id") != plan.get("plan_id"):
        raise ValueError(
            "Distillation result plan_id does not match the transaction plan"
        )
    validate_result_plan_semantics(root, plan, result)

    events = load_record_events(task_dir, task_id)
    validate_expected_record_digest(plan, record, events)
    bundle, persisted_candidate_digest, _ = load_persisted_candidate_bundle(
        task_dir, task_id, record, events
    )
    _, anchor_digest = candidate_bundle_anchor(record) or (None, None)
    if (
        result_candidate_digest != anchor_digest
        or result_candidate_digest != persisted_candidate_digest
        or result_candidate_digest != plan.get("expected_candidate_digest")
    ):
        raise ValueError(
            "Candidate digest audit chain mismatch between result, record anchor, "
            "persisted bundle, and transaction plan"
        )
    validate_plan_candidate_binding(plan, bundle, persisted_candidate_digest)

    persisted_result = read_json(transaction_result_path)
    if persisted_result != result or canonical_json(persisted_result) != canonical_json(
        result
    ):
        raise ValueError(
            "Distillation result does not match the transaction result artifact"
        )


def candidate_record_integrity_issues(root: Path) -> list[dict[str, str]]:
    """Report candidate/result audit-chain problems without modifying the store."""
    issues: list[dict[str, str]] = []
    records: dict[str, tuple[Path, Path, dict[str, Any]]] = {}
    try:
        for task_dir, record_path, record in iter_all_task_records(root):
            task_id = str(record.get("task_id", "<unknown>"))
            if task_id in records:
                issues.append(
                    {
                        "task_id": task_id,
                        "path": str(record_path),
                        "issue": "duplicate task record",
                    }
                )
                continue
            records[task_id] = (task_dir, record_path, record)
    except Exception as exc:
        return [
            {"task_id": "<unknown>", "path": str(root / "records"), "issue": str(exc)}
        ]

    for task_id, (task_dir, record_path, record) in sorted(records.items()):
        bundle_path = task_dir / CANDIDATE_BUNDLE_FILENAME
        result_path = task_dir / "distillation-result.json"
        try:
            anchor = candidate_bundle_anchor(record)
            if anchor is None:
                if bundle_path.exists() or bundle_path.is_symlink():
                    raise ValueError(
                        "candidate bundle exists without a task-record anchor"
                    )
                if result_path.exists() or result_path.is_symlink():
                    raise ValueError(
                        "distillation result exists without a candidate anchor"
                    )
                if record.get("distillation_status") in {"committed", "no-knowledge"}:
                    raise ValueError(
                        "terminal distillation record has no candidate anchor"
                    )
                continue
            events = load_record_events(task_dir, task_id)
            load_persisted_candidate_bundle(task_dir, task_id, record, events)
            if result_path.exists() or result_path.is_symlink():
                if result_path.is_symlink():
                    raise ValueError(
                        f"Distillation result cannot be a symlink: {result_path}"
                    )
                validate_result_candidate_chain(
                    root, task_dir, record, read_json(result_path)
                )
            elif record.get("distillation_status") in {"committed", "no-knowledge"}:
                raise ValueError("terminal distillation record has no result artifact")
            result_link = record.get("distillation_result")
            if result_link is not None and result_link != "distillation-result.json":
                raise ValueError("task record has an invalid distillation_result path")
        except Exception as exc:
            issues.append(
                {"task_id": task_id, "path": str(record_path), "issue": str(exc)}
            )

    transactions_root = root / ".meta" / "transactions"
    for status_path in sorted(transactions_root.glob("*/status.json")):
        task_label = "<unknown>"
        try:
            if status_path.is_symlink():
                raise ValueError(
                    f"Transaction status cannot be a symlink: {status_path}"
                )
            status = read_json(status_path)
            transaction_id, task_id, _, state = validate_transaction_status(
                root, status_path.parent, status
            )
            task_label = task_id
            transaction_result_path = status_path.parent / "result.json"
            if state != "committed":
                continue
            if transaction_result_path.is_symlink():
                raise ValueError(
                    f"Transaction result cannot be a symlink: {transaction_result_path}"
                )
            if not transaction_result_path.is_file():
                raise FileNotFoundError(
                    f"Committed transaction has no result artifact: "
                    f"{transaction_result_path}"
                )
            if task_id not in records:
                raise FileNotFoundError(
                    f"Task record not found for transaction task {task_id}"
                )
            task_dir, _, record = records[task_id]
            validate_result_candidate_chain(
                root, task_dir, record, read_json(transaction_result_path)
            )
        except Exception as exc:
            issues.append(
                {
                    "task_id": task_label,
                    "path": str(status_path),
                    "issue": str(exc),
                }
            )
    return issues


def sync_record_result(
    root: Path,
    task_dir: Path,
    record: dict[str, Any],
    result: dict[str, Any],
) -> None:
    validate_result_candidate_chain(root, task_dir, record, result)
    atomic_write_json(task_dir / "distillation-result.json", result)
    record["distillation_status"] = result["distillation_status"]
    record["distillation_result"] = "distillation-result.json"
    save_record(task_dir, record)


def command_start(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    title = require_string(args.title, "title")
    objective = require_string(args.objective, "objective")
    workspace = Path(args.workspace or Path.cwd()).expanduser().resolve()
    parent_task_id = args.parent_task_id
    if parent_task_id is not None:
        validate_task_id(parent_task_id)

    with write_lock(root, "start-record") as lock_token:
        incomplete = incomplete_transactions(root)
        candidate_issues = candidate_record_integrity_issues(root)
        reconciliations = (
            [] if candidate_issues else pending_record_reconciliations(root)
        )
        stranded = stranded_distillation_records(root)
        if incomplete or reconciliations or stranded or candidate_issues:
            raise RuntimeError(
                "Knowledge store requires recovery before starting a task: "
                + json.dumps(
                    {
                        "incomplete_transactions": incomplete,
                        "record_reconciliations": reconciliations,
                        "stranded_records": stranded,
                        "candidate_integrity_issues": candidate_issues,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        validate_derived_store(root)
        if parent_task_id is not None and parent_task_id not in all_record_ids(root):
            raise FileNotFoundError(f"Parent task record not found: {parent_task_id}")

        timestamp = datetime.now(timezone.utc)
        task_id = f"task-{slugify(title)}-{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        task_dir = active_task_dir(root, task_id)
        assert_write_lock_owned(root, lock_token)
        task_dir.joinpath("events").mkdir(parents=True)
        fsync_directory(task_dir)
        fsync_directory(task_dir.parent)
        record = {
            "schema_version": RECORD_SCHEMA,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "title": title,
            "objective": objective,
            "workspace": str(workspace),
            "task_status": "active",
            "distillation_status": "not-ready",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "outcome": None,
        }
        save_record(task_dir, record)

    return {
        "schema_version": RECORD_SCHEMA,
        "ok": True,
        "knowledge_root": str(root),
        "task_id": task_id,
        "task_record_path": str(task_dir / "task-record.json"),
        "recovery_warnings": {
            "incomplete_transactions": incomplete,
            "record_reconciliations": reconciliations,
            "stranded_records": stranded,
            "candidate_integrity_issues": candidate_issues,
        },
    }


def command_log(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "log-event"):
        task_dir, record = load_active_record(root, args.task)
        if record.get("task_status") != "active":
            raise RuntimeError(
                f"Task {args.task} is sealed; its factual record is immutable"
            )
        if args.kind not in EVENT_KINDS:
            raise ValueError(f"kind must be one of {sorted(EVENT_KINDS)}")
        text = require_string(args.text, "text")
        event_id = f"event-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_id": event_id,
            "task_id": args.task,
            "timestamp": utc_now(),
            "kind": args.kind,
            "text": text,
            "evidence": list(dict.fromkeys(args.evidence or [])),
            "span_id": args.span_id,
        }
        event_path = task_dir / "events" / f"{event_id}.json"
        atomic_write_json(event_path, event)
        return {
            "schema_version": EVENT_SCHEMA,
            "ok": True,
            "task_id": args.task,
            "event_id": event_id,
            "event_path": str(event_path),
        }


def command_seal(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "seal-record"):
        task_dir, record = load_active_record(root, args.task)
        if record.get("task_status") != "active":
            raise RuntimeError(f"Task {args.task} is already sealed")
        if args.status not in TASK_STATUSES - {"active"}:
            raise ValueError("status must be completed, failed, or cancelled")
        record["task_status"] = args.status
        record["distillation_status"] = "pending"
        record["sealed_at"] = utc_now()
        record["event_count"] = len(list((task_dir / "events").glob("*.json")))
        record["outcome"] = {
            "summary": require_string(args.summary, "summary"),
            "remaining": list(dict.fromkeys(args.remaining or [])),
        }
        save_record(task_dir, record)
        return {
            "schema_version": RECORD_SCHEMA,
            "ok": True,
            "task_id": args.task,
            "task_status": args.status,
            "distillation_status": "pending",
            "task_record_path": str(task_dir / "task-record.json"),
        }


def command_context(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "build-context"):
        task_dir, record = load_active_record(root, args.task)
        require_pending_distillation(record, args.task, "building distillation context")
        events = load_record_events(task_dir, args.task)
        record_digest = sealed_record_digest(record, events)
        catalog = read_json(root / ".meta" / "catalog.json")
        context = {
            "schema_version": "distill-task-knowledge.context.v1",
            "generated_at": utc_now(),
            "knowledge_root": str(root),
            "knowledge_tree": str(root / "knowledge"),
            "record_digest": record_digest,
            "record": record,
            "events": events,
            "catalog": catalog,
            "instructions": {
                "plan_schema": PLAN_SCHEMA,
                "allowed_operations": sorted(OPERATION_TYPES),
                "expected_record_digest_source": "record_digest",
                "candidate_bundle_schema": CANDIDATE_BUNDLE_SCHEMA,
                "expected_hash_source": "catalog.notes[].sha256",
                "expected_tree_digest_source": "catalog.tree_digest",
            },
        }
        context_path = task_dir / "distillation-context.json"
        atomic_write_json(context_path, context)
        return {
            "schema_version": context["schema_version"],
            "ok": True,
            "task_id": args.task,
            "context_path": str(context_path),
            "record_digest": record_digest,
            "catalog_path": str(root / ".meta" / "catalog.json"),
            "knowledge_tree": str(root / "knowledge"),
            "knowledge_items": len(catalog.get("notes", [])),
        }


def command_save_candidates(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "save-candidates"):
        task_dir, record = load_active_record(root, args.task)
        require_pending_distillation(record, args.task, "saving knowledge candidates")
        events = load_record_events(task_dir, args.task)
        bundle_path = Path(args.bundle).expanduser().resolve()
        submitted = validate_candidate_bundle(read_json(bundle_path), args.task)
        actual_record_digest = sealed_record_digest(record, events)
        if submitted["record_digest"] != actual_record_digest:
            raise RuntimeError(
                "Candidate bundle record_digest does not match the sealed task record: "
                f"expected {actual_record_digest}, found {submitted['record_digest']}; "
                "rebuild context and candidates"
            )
        candidate_digest = candidate_bundle_digest(submitted)
        persisted_path = task_dir / CANDIDATE_BUNDLE_FILENAME
        if persisted_path.is_symlink():
            raise ValueError(f"Candidate bundle cannot be a symlink: {persisted_path}")
        anchor = candidate_bundle_anchor(record)
        if anchor is None:
            if persisted_path.exists():
                raise RuntimeError(
                    "An unanchored candidate bundle already exists; refusing to adopt or "
                    "overwrite it because its integrity cannot be established"
                )
            record["candidate_bundle"] = CANDIDATE_BUNDLE_FILENAME
            record["candidate_digest"] = candidate_digest
            save_record(task_dir, record)
            atomic_write_json(persisted_path, submitted)
        else:
            anchor_path, anchor_digest = anchor
            if (
                anchor_path != CANDIDATE_BUNDLE_FILENAME
                or anchor_digest != candidate_digest
            ):
                raise RuntimeError(
                    f"Task {args.task} already has a different candidate bundle anchor"
                )
            if not persisted_path.exists():
                # This is the only safe retry window: the record anchor was durably written,
                # but the first atomic bundle write did not complete.
                atomic_write_json(persisted_path, submitted)
        persisted, persisted_digest, _ = load_persisted_candidate_bundle(
            task_dir, args.task, record, events
        )
        if persisted_digest != candidate_digest or persisted != submitted:
            raise RuntimeError(
                f"Task {args.task} already has a different persisted candidate bundle"
            )
        return {
            "schema_version": "distill-task-knowledge.candidates-saved.v1",
            "ok": True,
            "task_id": args.task,
            "record_digest": actual_record_digest,
            "candidate_digest": candidate_digest,
            "candidate_count": len(submitted["candidates"]),
            "no_knowledge_reason": submitted["no_knowledge_reason"],
            "candidate_path": str(persisted_path),
        }


def command_check_plan(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "check-distillation-plan"):
        return _command_check_plan_locked(args, root)


def _command_check_plan_locked(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    task_dir, record = load_active_record(root, args.task)
    require_pending_distillation(record, args.task, "checking a distillation plan")
    events = load_record_events(task_dir, args.task)
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    validate_plan(plan, args.task)
    validate_expected_record_digest(plan, record, events)
    bundle, candidate_digest, _ = load_persisted_candidate_bundle(
        task_dir, args.task, record, events
    )
    validate_plan_candidate_binding(plan, bundle, candidate_digest)
    validate_review_paths(root, plan)
    plan_hash = sha256_bytes(canonical_json(plan))
    touched_paths: list[str] = []

    with write_lock(root, f"check-plan:{args.task}"):
        task_dir, record = load_active_record(root, args.task)
        require_pending_distillation(record, args.task, "checking a distillation plan")
        events = load_record_events(task_dir, args.task)
        validate_expected_record_digest(plan, record, events)
        bundle, candidate_digest, _ = load_persisted_candidate_bundle(
            task_dir, args.task, record, events
        )
        validate_plan_candidate_binding(plan, bundle, candidate_digest)
        current_catalog = validate_and_catalog(root, root / "knowledge")
        validate_expected_tree_digest(plan, current_catalog)
        operations = plan["operations"]
        if operations:
            with tempfile.TemporaryDirectory(
                prefix="distill-task-knowledge-check-"
            ) as temporary:
                stage_tree = Path(temporary) / "knowledge"
                assert_no_symlinks(root / "knowledge")
                shutil.copytree(root / "knowledge", stage_tree, symlinks=True)
                assert_no_symlinks(stage_tree)
                for operation in operations:
                    touched_paths.extend(
                        apply_operation(stage_tree, operation, args.task)
                    )
                rebuild_indexes(stage_tree)
                proposed_catalog = validate_and_catalog(root, stage_tree)
        else:
            proposed_catalog = validate_and_catalog(root, root / "knowledge")

    return {
        "schema_version": "distill-task-knowledge.plan-check.v1",
        "ok": True,
        "task_id": args.task,
        "plan_id": plan["plan_id"],
        "plan_hash": plan_hash,
        "candidate_digest": candidate_digest,
        "operation_count": len(plan["operations"]),
        "touched_paths": sorted(set(touched_paths)),
        "proposed_knowledge_items": len(proposed_catalog.get("notes", [])),
        "no_knowledge_reason": plan.get("no_knowledge_reason"),
    }


def command_apply_plan(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "apply-distillation-plan"):
        return _command_apply_plan_locked(args, root)


def _command_apply_plan_locked(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    task_dir, record = load_active_record(root, args.task)
    if record.get("task_status") == "active":
        raise RuntimeError(f"Seal task {args.task} before applying a distillation plan")
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    validate_plan(plan, args.task)
    events = load_record_events(task_dir, args.task)
    validate_expected_record_digest(plan, record, events)
    bundle, candidate_digest, _ = load_persisted_candidate_bundle(
        task_dir, args.task, record, events
    )
    validate_plan_candidate_binding(plan, bundle, candidate_digest)
    validate_review_paths(root, plan)
    plan_hash = sha256_bytes(canonical_json(plan))
    transaction_id = distillation_transaction_id(args.task, plan_hash)
    transaction_dir = root / ".meta" / "transactions" / transaction_id

    existing_result_path = task_dir / "distillation-result.json"
    if existing_result_path.exists():
        existing_result = read_json(existing_result_path)
        if existing_result.get("plan_hash") != plan_hash:
            raise RuntimeError(
                f"Task {args.task} already has a different committed distillation plan"
            )
        sync_record_result(root, task_dir, record, existing_result)
        return existing_result

    require_pending_distillation(record, args.task, "applying a distillation plan")

    with write_lock(root, f"apply-plan:{args.task}") as lock_token:
        task_dir, record = load_active_record(root, args.task)
        existing_result_path = task_dir / "distillation-result.json"
        if existing_result_path.exists():
            existing_result = read_json(existing_result_path)
            if existing_result.get("plan_hash") != plan_hash:
                raise RuntimeError(
                    f"Task {args.task} already has a different committed distillation plan"
                )
            sync_record_result(root, task_dir, record, existing_result)
            return existing_result
        require_pending_distillation(record, args.task, "applying a distillation plan")
        events = load_record_events(task_dir, args.task)
        validate_expected_record_digest(plan, record, events)
        bundle, candidate_digest, _ = load_persisted_candidate_bundle(
            task_dir, args.task, record, events
        )
        validate_plan_candidate_binding(plan, bundle, candidate_digest)
        current_catalog = validate_and_catalog(root, root / "knowledge")
        validate_expected_tree_digest(plan, current_catalog)
        transaction_dir.mkdir(parents=True, exist_ok=True)
        if transaction_dir.is_symlink():
            raise ValueError(
                f"Transaction directory cannot be a symlink: {transaction_dir}"
            )
        assert_transaction_components_safe(root, transaction_dir)
        fsync_directory(transaction_dir.parent)
        status_path = transaction_status_path(transaction_dir)
        if status_path.exists():
            if status_path.is_symlink():
                raise ValueError(
                    f"Transaction status cannot be a symlink: {status_path}"
                )
            status = read_json(status_path)
            stored_id, stored_task, stored_hash, stored_state = (
                validate_transaction_status(root, transaction_dir, status)
            )
            if (
                stored_id != transaction_id
                or stored_task != args.task
                or stored_hash != plan_hash
            ):
                raise RuntimeError(
                    f"Transaction metadata does not match plan: {transaction_dir}"
                )
            if stored_state == "committed":
                result_path = transaction_dir / "result.json"
                if result_path.is_symlink():
                    raise ValueError(
                        f"Transaction result cannot be a symlink: {result_path}"
                    )
                result = read_json(result_path)
                sync_record_result(root, task_dir, record, result)
                return result
            if stored_state != "rolled-back":
                raise RuntimeError(
                    f"Transaction {transaction_id} is {stored_state!r}; run recover first"
                )
            assert_transaction_components_safe(root, transaction_dir)
            shutil.rmtree(transaction_dir)
            fsync_directory(transaction_dir.parent)
            transaction_dir.mkdir(parents=True)
            fsync_directory(transaction_dir.parent)

        # The durable transaction marker must precede the mutable record state. If
        # the process stops at any later instruction, recovery can find this work.
        write_transaction_status(
            transaction_dir, transaction_id, args.task, plan_hash, "planned"
        )
        atomic_write_json(transaction_dir / "plan.json", plan)
        atomic_write_json(task_dir / "distillation-plan.json", plan)
        record["distillation_status"] = "planned"
        save_record(task_dir, record)

        try:
            operations = plan["operations"]
            touched_paths: list[str] = []
            if operations:
                stage_tree = transaction_dir / "stage" / "knowledge"
                assert_transaction_components_safe(root, transaction_dir)
                assert_no_symlinks(root / "knowledge")
                shutil.copytree(root / "knowledge", stage_tree, symlinks=True)
                assert_no_symlinks(stage_tree)
                for operation in operations:
                    touched_paths.extend(
                        apply_operation(stage_tree, operation, args.task)
                    )
                rebuild_indexes(stage_tree)
                catalog = validate_and_catalog(root, stage_tree)
                fsync_tree(stage_tree)
                write_transaction_status(
                    transaction_dir, transaction_id, args.task, plan_hash, "prepared"
                )
            else:
                catalog = validate_and_catalog(root, root / "knowledge")

            result = {
                "schema_version": RESULT_SCHEMA,
                "ok": True,
                "task_id": args.task,
                "plan_id": plan["plan_id"],
                "plan_hash": plan_hash,
                "candidate_digest": candidate_digest,
                "transaction_id": transaction_id,
                "distillation_status": "committed" if operations else "no-knowledge",
                "operation_count": len(operations),
                "touched_paths": sorted(set(touched_paths)),
                "no_knowledge_reason": plan.get("no_knowledge_reason"),
                "knowledge_root": str(root),
                "catalog_path": str(root / ".meta" / "catalog.json"),
                "committed_at": utc_now(),
            }
            atomic_write_json(transaction_dir / "result.json", result)
            if operations:
                commit_staged_tree(
                    root,
                    transaction_dir,
                    transaction_id,
                    args.task,
                    plan_hash,
                    catalog,
                    lock_token,
                )
            else:
                write_transaction_status(
                    transaction_dir, transaction_id, args.task, plan_hash, "committed"
                )
            sync_record_result(root, task_dir, record, result)
            return result
        except Exception as exc:
            status = read_json(status_path)
            state = status.get("status")
            atomic_write_json(
                transaction_dir / "error.json",
                {
                    "schema_version": TRANSACTION_SCHEMA,
                    "transaction_id": transaction_id,
                    "task_id": args.task,
                    "status": state,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "failed_at": utc_now(),
                },
            )
            if state in {"planned", "prepared", "rolled-back"}:
                assert_transaction_components_safe(root, transaction_dir)
                for residual in (
                    transaction_dir / "stage",
                    transaction_dir / "before",
                    transaction_dir / "failed",
                ):
                    if residual.exists():
                        shutil.rmtree(residual)
                fsync_directory(transaction_dir)
                write_transaction_status(
                    transaction_dir, transaction_id, args.task, plan_hash, "rolled-back"
                )
                record["distillation_status"] = "pending"
            else:
                record["distillation_status"] = "recovery-required"
            save_record(task_dir, record)
            raise


def command_archive(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root)
    with task_lock(root, args.task, "archive-record"):
        with write_lock(root, f"archive-record:{args.task}") as lock_token:
            incomplete = incomplete_transactions(root)
            candidate_issues = candidate_record_integrity_issues(root)
            reconciliations = (
                [] if candidate_issues else pending_record_reconciliations(root)
            )
            stranded = stranded_distillation_records(root)
            if incomplete or reconciliations or stranded or candidate_issues:
                raise RuntimeError(
                    "Knowledge store requires recovery before archiving a task: "
                    + json.dumps(
                        {
                            "incomplete_transactions": incomplete,
                            "record_reconciliations": reconciliations,
                            "stranded_records": stranded,
                            "candidate_integrity_issues": candidate_issues,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            validate_derived_store(root)

            task_dir, record = load_active_record(root, args.task)
            if record.get("task_status") == "active":
                raise RuntimeError(f"Task {args.task} must be sealed before archiving")
            if record.get("distillation_status") not in {"committed", "no-knowledge"}:
                raise RuntimeError(
                    f"Task {args.task} must have a committed or no-knowledge distillation before archiving"
                )
            archived_at = datetime.now(timezone.utc)
            record["archived_at"] = utc_now()
            assert_write_lock_owned(root, lock_token)
            save_record(task_dir, record)
            archive_parent = mkdir_chain_without_symlinks(
                root / "records" / "archive",
                [
                    archived_at.strftime("%Y"),
                    archived_at.strftime("%m"),
                    archived_at.strftime("%d"),
                ],
            )
            archive_dir = archive_parent / args.task
            if archive_dir.exists():
                raise FileExistsError(f"Archive target already exists: {archive_dir}")
            os.replace(task_dir, archive_dir)
            fsync_directory(task_dir.parent)
            fsync_directory(archive_dir.parent)
            return {
                "schema_version": RECORD_SCHEMA,
                "ok": True,
                "task_id": args.task,
                "task_status": record["task_status"],
                "distillation_status": record["distillation_status"],
                "archive_path": str(archive_dir),
            }


def rolled_back_transaction_needs_recovery(root: Path, task_id: str) -> bool:
    task_dir = active_task_dir(root, task_id)
    record_path = task_dir / "task-record.json"
    if not record_path.is_file():
        return False
    loaded_task_dir, record = load_active_record(root, task_id)
    result_path = loaded_task_dir / "distillation-result.json"
    if result_path.is_symlink():
        raise ValueError(f"Distillation result cannot be a symlink: {result_path}")
    return (
        record.get("distillation_status") in {"planned", "recovery-required"}
        and not result_path.exists()
    )


def transaction_inventory(
    root: Path,
) -> tuple[list[tuple[Path, str, str, str, str]], list[dict[str, Any]]]:
    transactions_root = root / ".meta" / "transactions"
    if transactions_root.is_symlink():
        raise ValueError(
            f"Transactions directory cannot be a symlink: {transactions_root}"
        )
    if not transactions_root.exists():
        return [], []

    statuses: list[tuple[Path, str, str, str, str]] = []
    orphans: list[dict[str, Any]] = []
    for transaction_dir in sorted(transactions_root.iterdir()):
        if transaction_dir.is_symlink() or not transaction_dir.is_dir():
            raise ValueError(
                f"Transaction entry must be a real directory: {transaction_dir}"
            )
        transaction_id = validate_transaction_id(transaction_dir.name)
        status_path = transaction_status_path(transaction_dir)
        if status_path.is_symlink():
            raise ValueError(f"Transaction status cannot be a symlink: {status_path}")
        if not status_path.exists():
            orphans.append(
                {
                    "transaction_id": transaction_id,
                    "task_id": None,
                    "status": "orphan",
                    "status_path": None,
                    "transaction_path": str(transaction_dir),
                    "empty": next(transaction_dir.iterdir(), None) is None,
                }
            )
            continue
        status = read_json(status_path)
        stored_id, task_id, plan_hash, state = validate_transaction_status(
            root, transaction_dir, status
        )
        statuses.append((status_path, stored_id, task_id, plan_hash, state))
    return statuses, orphans


def incomplete_transactions(root: Path) -> list[dict[str, Any]]:
    statuses, orphans = transaction_inventory(root)
    result: list[dict[str, Any]] = list(orphans)
    for status_path, transaction_id, task_id, _plan_hash, state in statuses:
        needs_recovery = state not in {"committed", "rolled-back"}
        reason: str | None = None
        if state == "committed":
            transaction_result = status_path.parent / "result.json"
            needs_recovery = (
                transaction_result.is_symlink() or not transaction_result.is_file()
            )
            if needs_recovery:
                reason = "committed transaction has no real result artifact"
        if state == "rolled-back":
            needs_recovery = rolled_back_transaction_needs_recovery(root, task_id)
        if needs_recovery:
            item = {
                "transaction_id": transaction_id,
                "task_id": task_id,
                "status": state,
                "status_path": str(status_path),
            }
            if reason is not None:
                item["reason"] = reason
            result.append(item)
    return result


def stranded_distillation_records(root: Path) -> list[dict[str, Any]]:
    statuses, _orphans = transaction_inventory(root)
    transaction_tasks = {task_id for _, _, task_id, _, _ in statuses}
    stranded: list[dict[str, Any]] = []
    for record_path, record in iter_active_records(root):
        task_id = record.get("task_id")
        state = record.get("distillation_status")
        if not isinstance(task_id, str) or state not in {
            "planned",
            "recovery-required",
        }:
            continue
        task_dir = record_path.parent
        result_path = task_dir / "distillation-result.json"
        if result_path.is_symlink():
            raise ValueError(f"Distillation result cannot be a symlink: {result_path}")
        if task_id not in transaction_tasks and not result_path.is_file():
            stranded.append(
                {
                    "task_id": task_id,
                    "distillation_status": state,
                    "record_path": str(record_path),
                    "reason": "no matching transaction or distillation result",
                }
            )
    return stranded


def pending_record_reconciliations(root: Path) -> list[dict[str, Any]]:
    committed_by_task: dict[str, tuple[str, Path]] = {}
    statuses, _orphans = transaction_inventory(root)
    for status_path, transaction_id, task_id, plan_hash, state in statuses:
        result_path = status_path.parent / "result.json"
        if result_path.is_symlink():
            raise ValueError(f"Transaction result cannot be a symlink: {result_path}")
        if state == "committed" and result_path.is_file():
            result = read_json(result_path)
            task_dir, _, record = find_task_record(root, task_id)
            validate_result_candidate_chain(root, task_dir, record, result)
            if result.get("plan_hash") != plan_hash:
                raise ValueError(
                    f"Committed transaction result does not match {status_path}"
                )
            if task_id in committed_by_task:
                raise ValueError(
                    f"Multiple committed transactions found for task {task_id}"
                )
            committed_by_task[task_id] = (transaction_id, result_path)

    result: list[dict[str, Any]] = []
    for record_path, record in iter_active_records(root):
        task_id = record.get("task_id")
        if (
            isinstance(task_id, str)
            and record.get("distillation_status") in {"planned", "recovery-required"}
            and task_id in committed_by_task
        ):
            transaction_id, result_path = committed_by_task[task_id]
            result.append(
                {
                    "task_id": task_id,
                    "transaction_id": transaction_id,
                    "record_path": str(record_path),
                    "result_path": str(result_path),
                }
            )
    return result


def reconcile_committed_records(root: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in pending_record_reconciliations(root):
        task_id = validate_task_id(require_string(item.get("task_id"), "task_id"))
        with task_lock(root, task_id, "reconcile-record-result"):
            task_dir, record = load_active_record(root, task_id)
            if task_dir / "task-record.json" != Path(item["record_path"]):
                raise ValueError(
                    f"Reconciliation record path changed for task {task_id}"
                )
            result_path = Path(item["result_path"])
            if result_path.is_symlink():
                raise ValueError(
                    f"Transaction result cannot be a symlink: {result_path}"
                )
            result = read_json(result_path)
            sync_record_result(root, task_dir, record, result)
        actions.append(
            {
                "transaction_id": item["transaction_id"],
                "task_id": item["task_id"],
                "action": "synchronized-record-result",
            }
        )
    return actions


def reset_stranded_distillation_records(root: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in stranded_distillation_records(root):
        task_id = validate_task_id(require_string(item.get("task_id"), "task_id"))
        with task_lock(root, task_id, "reset-stranded-record"):
            # Re-scan while holding the record lock. A transaction or result that appeared
            # since discovery makes an automatic reset unsafe.
            current = {
                candidate["task_id"]: candidate
                for candidate in stranded_distillation_records(root)
            }
            if task_id not in current:
                continue
            task_dir, record = load_active_record(root, task_id)
            if record.get("distillation_status") not in {
                "planned",
                "recovery-required",
            }:
                continue
            record["distillation_status"] = "pending"
            save_record(task_dir, record)
        actions.append(
            {
                "task_id": task_id,
                "action": "reset-stranded-record-to-pending",
            }
        )
    return actions


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root, allow_missing_derived=True)
    active_records: list[dict[str, Any]] = []
    for record_path, record in iter_active_records(root):
        active_records.append(
            {
                "task_id": record.get("task_id"),
                "title": record.get("title"),
                "task_status": record.get("task_status"),
                "distillation_status": record.get("distillation_status"),
                "record_path": str(record_path),
            }
        )
    incomplete = incomplete_transactions(root)
    candidate_issues = candidate_record_integrity_issues(root)
    reconciliations = [] if candidate_issues else pending_record_reconciliations(root)
    stranded = stranded_distillation_records(root)
    derived_health, catalog = derived_store_health(root)
    return {
        "schema_version": STORE_SCHEMA,
        "ok": (
            not incomplete
            and not reconciliations
            and not stranded
            and not candidate_issues
            and derived_health["ok"]
        ),
        "knowledge_root": str(root),
        "active_records": active_records,
        "incomplete_transactions": incomplete,
        "record_reconciliations": reconciliations,
        "stranded_records": stranded,
        "candidate_integrity_issues": candidate_issues,
        "derived_health": derived_health,
        "knowledge_items": (
            len(catalog.get("notes", [])) if catalog is not None else None
        ),
    }


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_stale_lock(root: Path, force: bool) -> bool:
    lock_dir = root / ".meta" / "locks" / "knowledge-write"
    with lock_transition(lock_dir):
        if not lock_dir.exists():
            return False
        owner = lock_owner(lock_dir)
        same_host = owner.get("hostname") == socket.gethostname()
        pid = owner.get("pid")
        alive = isinstance(pid, int) and same_host and process_is_alive(pid)
        if alive:
            raise RuntimeError(
                f"Refusing to clear live lock owned by PID {pid}: {lock_dir}"
            )
        if not same_host and not force:
            raise RuntimeError(
                f"Lock belongs to another or unknown host; verify it is stale and use "
                f"recover --force: {owner}"
            )
        if not force and "pid" not in owner:
            raise RuntimeError(
                f"Lock owner metadata is incomplete; use recover --force: {lock_dir}"
            )
        if lock_owner(lock_dir) != owner:
            raise RuntimeError(f"Lock ownership changed while recovering: {lock_dir}")
        shutil.rmtree(lock_dir)
        fsync_directory(lock_dir.parent)
        return True


def reset_record_after_rollback(root: Path, task_id: str) -> None:
    task_dir = active_task_dir(root, task_id)
    if task_dir.is_symlink():
        raise ValueError(f"Task record directory cannot be a symlink: {task_dir}")
    record_path = task_dir / "task-record.json"
    if not record_path.is_file():
        return
    with task_lock(root, task_id, "reset-rolled-back-record"):
        task_dir, record = load_active_record(root, task_id)
        result_path = task_dir / "distillation-result.json"
        if result_path.is_symlink():
            raise ValueError(f"Distillation result cannot be a symlink: {result_path}")
        if not result_path.exists():
            record["distillation_status"] = "pending"
            save_record(task_dir, record)


def recover_orphan_transaction(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    transaction_id = validate_transaction_id(
        require_string(item.get("transaction_id"), "transaction_id")
    )
    transactions_root = root / ".meta" / "transactions"
    transaction_dir = transactions_root / transaction_id
    if transaction_dir.is_symlink() or transaction_dir.parent != transactions_root:
        raise ValueError(f"Unsafe orphan transaction path: {transaction_dir}")
    if not transaction_dir.is_dir():
        raise RuntimeError(
            f"Orphan transaction directory disappeared: {transaction_dir}"
        )
    status_path = transaction_status_path(transaction_dir)
    if status_path.exists() or status_path.is_symlink():
        raise RuntimeError(
            f"Orphan transaction acquired a status during recovery: {transaction_dir}"
        )
    if next(transaction_dir.iterdir(), None) is not None:
        raise RuntimeError(
            f"Refusing to remove non-empty orphan transaction directory: {transaction_dir}"
        )
    transaction_dir.rmdir()
    fsync_directory(transactions_root)
    return {
        "transaction_id": transaction_id,
        "action": "removed-empty-orphan-transaction",
    }


def recover_one(
    root: Path,
    transaction_dir: Path,
    status: dict[str, Any],
) -> dict[str, Any]:
    transaction_id, task_id, plan_hash, state = validate_transaction_status(
        root, transaction_dir, status
    )
    live = root / "knowledge"
    stage = transaction_dir / "stage" / "knowledge"
    backup = transaction_dir / "before" / "knowledge"
    backup_catalog = transaction_dir / "before" / "catalog.json"
    failed = transaction_dir / "failed" / "knowledge"
    assert_transaction_components_safe(root, transaction_dir)
    for path in (
        transaction_dir / "stage",
        stage,
        transaction_dir / "before",
        backup,
        transaction_dir / "result.json",
    ):
        if path.is_symlink():
            raise ValueError(f"Transaction component cannot be a symlink: {path}")

    if state == "rolled-back":
        if not live.is_dir() or backup.exists():
            raise RuntimeError(
                f"Rolled-back transaction {transaction_id} has an inconsistent tree state: "
                f"live={live.exists()} backup={backup.exists()}"
            )
        for residual in (
            transaction_dir / "stage",
            transaction_dir / "before",
            transaction_dir / "failed",
        ):
            if residual.exists():
                shutil.rmtree(residual)
        fsync_directory(transaction_dir)
        write_transaction_status(
            transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
        )
        reset_record_after_rollback(root, task_id)
        return {
            "transaction_id": transaction_id,
            "action": "reconciled-rolled-back-record",
        }

    if state in {"planned", "prepared"}:
        if stage.exists():
            shutil.rmtree(stage)
        write_transaction_status(
            transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
        )
        reset_record_after_rollback(root, task_id)
        return {
            "transaction_id": transaction_id,
            "action": "rolled-back-uncommitted-stage",
        }

    if state in {"committing", "recovery-required"}:
        if (
            live.is_dir()
            and not stage.exists()
            and not backup.exists()
            and failed.is_dir()
            and backup_catalog.is_file()
        ):
            expected_catalog = read_json(backup_catalog)
            actual_catalog = validate_and_catalog(root, live)
            expected_without_timestamp = dict(expected_catalog)
            actual_without_timestamp = dict(actual_catalog)
            expected_without_timestamp.pop("generated_at", None)
            actual_without_timestamp.pop("generated_at", None)
            if expected_without_timestamp != actual_without_timestamp:
                raise RuntimeError(
                    f"Transaction {transaction_id} has rollback artifacts, but the live tree "
                    "does not match the saved pre-transaction catalog"
                )
            atomic_write_text(
                root / ".meta" / "catalog.json",
                backup_catalog.read_text(encoding="utf-8"),
            )
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
            )
            for residual in (
                transaction_dir / "stage",
                transaction_dir / "before",
                transaction_dir / "failed",
            ):
                if residual.exists():
                    shutil.rmtree(residual)
            fsync_directory(transaction_dir)
            reset_record_after_rollback(root, task_id)
            return {
                "transaction_id": transaction_id,
                "action": "completed-automatic-rollback",
            }
        if backup.exists() and not live.exists():
            if stage.exists():
                shutil.rmtree(stage)
            os.replace(backup, live)
            catalog = validate_and_catalog(root, live)
            atomic_write_json(root / ".meta" / "catalog.json", catalog)
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
            )
            reset_record_after_rollback(root, task_id)
            return {"transaction_id": transaction_id, "action": "restored-before-tree"}
        if backup.exists() and live.exists() and not stage.exists():
            result_path = transaction_dir / "result.json"
            if not result_path.is_file():
                raise RuntimeError(
                    f"Transaction {transaction_id} completed a tree swap but has no result.json"
                )
            task_dir, _, record = find_task_record(root, task_id)
            validate_result_candidate_chain(
                root,
                task_dir,
                record,
                read_json(result_path),
                allowed_transaction_states=frozenset(
                    {"committing", "recovery-required"}
                ),
            )
            rebuild_indexes(live)
            catalog = validate_and_catalog(root, live)
            atomic_write_json(root / ".meta" / "catalog.json", catalog)
            shutil.rmtree(backup)
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "committed"
            )
            return {"transaction_id": transaction_id, "action": "completed-tree-swap"}
        if not backup.exists() and live.exists() and stage.exists():
            shutil.rmtree(stage)
            write_transaction_status(
                transaction_dir, transaction_id, task_id, plan_hash, "rolled-back"
            )
            reset_record_after_rollback(root, task_id)
            return {
                "transaction_id": transaction_id,
                "action": "rolled-back-before-swap",
            }
        raise RuntimeError(
            f"Transaction {transaction_id} is ambiguous: "
            f"live={live.exists()} stage={stage.exists()} backup={backup.exists()}"
        )

    raise RuntimeError(
        f"Cannot recover transaction {transaction_id} from state {state!r}"
    )


def command_recover(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    assert_fixed_store_paths(root)
    schema_path = root / ".meta" / "schema-version"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Knowledge store not found: {root}")
    actual_schema = schema_path.read_text(encoding="utf-8").strip()
    if actual_schema != STORE_SCHEMA:
        raise ValueError(
            f"Unsupported knowledge store schema {actual_schema!r}; expected {STORE_SCHEMA!r}"
        )
    (root / ".meta" / "locks").mkdir(parents=True, exist_ok=True)
    cleared_lock = clear_stale_lock(root, args.force)
    actions: list[dict[str, Any]] = []
    with write_lock(root, "recover") as lock_token:
        for item in incomplete_transactions(root):
            assert_write_lock_owned(root, lock_token)
            if item.get("status") == "orphan":
                actions.append(recover_orphan_transaction(root, item))
                continue
            status_path_value = item.get("status_path")
            if not isinstance(status_path_value, str):
                raise ValueError(f"Incomplete transaction has no status path: {item}")
            status_path = Path(status_path_value)
            status = read_json(status_path)
            actions.append(recover_one(root, status_path.parent, status))
        actions.extend(reconcile_committed_records(root))
        actions.extend(reset_stranded_distillation_records(root))
        if not (root / "knowledge").is_dir():
            raise RuntimeError(
                f"Recovery did not restore the knowledge tree: {root / 'knowledge'}"
            )
        assert_write_lock_owned(root, lock_token)
        rebuild_indexes(root / "knowledge")
        catalog = validate_and_catalog(root, root / "knowledge")
        atomic_write_json(root / ".meta" / "catalog.json", catalog)
        remaining_candidate_issues = candidate_record_integrity_issues(root)
        if remaining_candidate_issues:
            raise RuntimeError(
                "Recovery cannot automatically repair candidate/result audit-chain "
                "integrity failures: "
                + json.dumps(
                    remaining_candidate_issues,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    return {
        "schema_version": STORE_SCHEMA,
        "ok": True,
        "knowledge_root": str(root),
        "cleared_lock": cleared_lock,
        "actions": actions,
    }


def command_doctor(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    ensure_store(root, allow_missing_derived=args.repair)
    with write_lock(
        root, "doctor-repair" if args.repair else "doctor-check"
    ) as lock_token:
        if args.repair:
            assert_write_lock_owned(root, lock_token)
            rebuild_indexes(root / "knowledge")
            catalog = validate_and_catalog(root, root / "knowledge")
            atomic_write_json(root / ".meta" / "catalog.json", catalog)
        catalog = validate_derived_store(root)
        incomplete = incomplete_transactions(root)
        candidate_issues = candidate_record_integrity_issues(root)
        reconciliations = (
            [] if candidate_issues else pending_record_reconciliations(root)
        )
        stranded = stranded_distillation_records(root)
        derived_health = {
            "ok": True,
            "tree_digest": catalog["tree_digest"],
            "knowledge_items": len(catalog.get("notes", [])),
        }
    return {
        "schema_version": STORE_SCHEMA,
        "ok": not incomplete
        and not reconciliations
        and not stranded
        and not candidate_issues,
        "knowledge_root": str(root),
        "repair_performed": bool(args.repair),
        "knowledge_items": len(catalog.get("notes", [])),
        "derived_health": derived_health,
        "incomplete_transactions": incomplete,
        "record_reconciliations": reconciliations,
        "stranded_records": stranded,
        "candidate_integrity_issues": candidate_issues,
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit(
            {
                "schema_version": "distill-task-knowledge.error.v1",
                "ok": False,
                "command": None,
                "error_type": "ArgumentError",
                "error": message,
            }
        )
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description="Record tasks and transactionally maintain a filesystem knowledge tree."
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Knowledge root. Defaults to TASK_KNOWLEDGE_ROOT or <cwd>/.knowledge.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create an active task record.")
    start.add_argument("--title", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--workspace", default=None)
    start.add_argument("--parent-task-id", default=None)
    start.set_defaults(handler=command_start)

    log = subparsers.add_parser("log", help="Append an immutable task event.")
    log.add_argument("--task", required=True)
    log.add_argument("--kind", required=True, choices=sorted(EVENT_KINDS))
    log.add_argument("--text", required=True)
    log.add_argument("--evidence", action="append", default=[])
    log.add_argument("--span-id", default=None)
    log.set_defaults(handler=command_log)

    seal = subparsers.add_parser("seal", help="Seal task facts before distillation.")
    seal.add_argument("--task", required=True)
    seal.add_argument(
        "--status", required=True, choices=["completed", "failed", "cancelled"]
    )
    seal.add_argument("--summary", required=True)
    seal.add_argument("--remaining", action="append", default=[])
    seal.set_defaults(handler=command_seal)

    context = subparsers.add_parser(
        "context", help="Build compact distillation context."
    )
    context.add_argument("--task", required=True)
    context.set_defaults(handler=command_context)

    save_candidates = subparsers.add_parser(
        "save-candidates",
        help="Validate and persist the sealed distillation candidate bundle.",
    )
    save_candidates.add_argument("--task", required=True)
    save_candidates.add_argument("--bundle", required=True)
    save_candidates.set_defaults(handler=command_save_candidates)

    check_plan = subparsers.add_parser(
        "check-plan",
        help="Validate and stage a plan without changing the store or record.",
    )
    check_plan.add_argument("--task", required=True)
    check_plan.add_argument("--plan", required=True)
    check_plan.set_defaults(handler=command_check_plan)

    apply_plan = subparsers.add_parser(
        "apply-plan",
        help="Validate and transactionally apply a semantic distillation plan.",
    )
    apply_plan.add_argument("--task", required=True)
    apply_plan.add_argument("--plan", required=True)
    apply_plan.set_defaults(handler=command_apply_plan)

    archive = subparsers.add_parser(
        "archive", help="Archive a sealed and distilled task record."
    )
    archive.add_argument("--task", required=True)
    archive.set_defaults(handler=command_archive)

    status = subparsers.add_parser(
        "status", help="Show active records and recovery warnings."
    )
    status.set_defaults(handler=command_status)

    recover = subparsers.add_parser(
        "recover", help="Recover incomplete knowledge transactions."
    )
    recover.add_argument(
        "--force",
        action="store_true",
        help="Clear a verified-stale lock from another or unknown host; live local locks are refused.",
    )
    recover.set_defaults(handler=command_recover)

    doctor = subparsers.add_parser(
        "doctor", help="Validate the knowledge tree and catalog."
    )
    doctor.add_argument(
        "--repair",
        action="store_true",
        help="Regenerate derived indexes and catalog before validation.",
    )
    doctor.set_defaults(handler=command_doctor)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except Exception as exc:
        emit(
            {
                "schema_version": "distill-task-knowledge.error.v1",
                "ok": False,
                "command": args.command,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
