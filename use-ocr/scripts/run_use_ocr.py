#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import build_pipeline_output as pipeline_builder


class DocumentSummary(TypedDict):
    document_id: str
    source_path: str | None
    parsed_dir: str
    pipeline_output_path: str


class RunSummary(TypedDict):
    schema_version: str
    ok: bool
    input_path: str
    output_root: str
    parsed_root: str
    run_summary_path: str
    used_temporary_output: bool
    documents: list[DocumentSummary]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run local GLM-OCR and normalize outputs. Defaults to a temporary "
            "directory under /tmp unless --output is provided."
        )
    )
    parser.add_argument("input", help="Input file or directory to parse")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output root directory. Defaults to /tmp/use-ocr-<document>-<token>. "
            "When provided, results are written to this directory."
        ),
    )
    parser.add_argument(
        "--keep-layout-vis",
        action="store_true",
        help="Keep layout visualization artifacts instead of skipping them.",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="Skip the setup script when the local OCR environment is already ready.",
    )
    return parser.parse_args()


def skill_base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    preferred_paths = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    existing_path = env.get("PATH", "")
    env["PATH"] = ":".join(
        [*preferred_paths, existing_path] if existing_path else preferred_paths
    )
    return env


def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "document"


def default_output_root(input_path: Path) -> Path:
    raw_name = input_path.stem if input_path.is_file() else input_path.name
    slug = slugify_name(raw_name)
    token = uuid4().hex[:8]
    return Path("/tmp") / f"use-ocr-{slug}-{token}"


def resolve_output_root(output_arg: str | None, input_path: Path) -> tuple[Path, bool]:
    if output_arg is None:
        return default_output_root(input_path), True
    return Path(output_arg).expanduser().resolve(), False


def run_command(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(
        command,
        check=True,
        env=env,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )


def run_setup_if_needed(base_dir: Path, env: dict[str, str], skip_setup: bool) -> None:
    if skip_setup:
        return
    run_command(["bash", str(base_dir / "scripts" / "setup-local-ocr.sh")], env)


def run_glmocr_parse(
    input_path: Path,
    parsed_root: Path,
    env: dict[str, str],
    keep_layout_vis: bool,
) -> None:
    config_path = Path.home() / ".local" / "glm-ocr" / "config.yaml"
    command = [
        "glmocr",
        "parse",
        str(input_path),
        "--config",
        str(config_path),
        "--mode",
        "selfhosted",
        "--output",
        str(parsed_root),
    ]
    if not keep_layout_vis:
        command.append("--no-layout-vis")
    run_command(command, env)


def is_parsed_document_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_markdown = any(path.glob("*.md"))
    has_regions_json = any(
        candidate.name != "pipeline-output.json" and candidate.suffix == ".json"
        for candidate in path.glob("*.json")
    )
    return has_markdown and has_regions_json


def collect_parsed_document_dirs(parsed_root: Path, input_path: Path) -> list[Path]:
    if input_path.is_file():
        expected_dir = parsed_root / input_path.stem
        if is_parsed_document_dir(expected_dir):
            return [expected_dir]

    return sorted(
        path for path in parsed_root.iterdir() if path.is_dir() and is_parsed_document_dir(path)
    )


def resolve_document_source(input_path: Path, parsed_dir: Path) -> Path | None:
    if input_path.is_file():
        return input_path.resolve()

    candidate_suffixes = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"]
    for suffix in candidate_suffixes:
        candidate_path = input_path / f"{parsed_dir.name}{suffix}"
        if candidate_path.exists():
            return candidate_path.resolve()
    return None


def write_pipeline_output(
    parsed_dir: Path,
    source_path: Path | None,
) -> Path:
    output_path = parsed_dir / "pipeline-output.json"
    pipeline_output = pipeline_builder.build_pipeline_output(
        parsed_dir=parsed_dir,
        output_path=output_path,
        source_path=source_path,
    )
    output_path.write_text(
        json.dumps(pipeline_output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def build_run_summary(
    input_path: Path,
    output_root: Path,
    parsed_root: Path,
    document_dirs: list[Path],
) -> RunSummary:
    documents: list[DocumentSummary] = []
    for parsed_dir in document_dirs:
        source_path = resolve_document_source(input_path, parsed_dir)
        pipeline_output_path = write_pipeline_output(parsed_dir, source_path)
        documents.append(
            {
                "document_id": parsed_dir.name,
                "source_path": str(source_path) if source_path is not None else None,
                "parsed_dir": str(parsed_dir),
                "pipeline_output_path": str(pipeline_output_path),
            }
        )

    run_summary_path = output_root / "run-summary.json"
    return {
        "schema_version": "use-ocr.run.v1",
        "ok": True,
        "input_path": str(input_path),
        "output_root": str(output_root),
        "parsed_root": str(parsed_root),
        "run_summary_path": str(run_summary_path),
        "used_temporary_output": str(output_root).startswith("/tmp/use-ocr-"),
        "documents": documents,
    }


def write_run_summary(summary: RunSummary) -> None:
    run_summary_path = Path(summary["run_summary_path"])
    run_summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    output_root, used_temporary_output = resolve_output_root(args.output, input_path)
    parsed_root = output_root / "parsed"
    output_root.mkdir(parents=True, exist_ok=True)
    parsed_root.mkdir(parents=True, exist_ok=True)

    base_dir = skill_base_dir()
    env = build_env()
    run_setup_if_needed(base_dir=base_dir, env=env, skip_setup=args.skip_setup)
    run_glmocr_parse(
        input_path=input_path,
        parsed_root=parsed_root,
        env=env,
        keep_layout_vis=args.keep_layout_vis,
    )

    document_dirs = collect_parsed_document_dirs(parsed_root=parsed_root, input_path=input_path)
    if not document_dirs:
        raise FileNotFoundError(f"No parsed document directories found inside {parsed_root}")

    summary = build_run_summary(
        input_path=input_path,
        output_root=output_root,
        parsed_root=parsed_root,
        document_dirs=document_dirs,
    )
    summary["used_temporary_output"] = used_temporary_output
    write_run_summary(summary)

    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
