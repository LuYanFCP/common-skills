#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict, cast


type RawJson = dict[str, Any]


class NormalizedRegion(TypedDict):
    page_index: int
    region_index: int
    label: str
    native_label: str | None
    bbox_2d: list[int] | None
    content: str | None


class PageOutput(TypedDict):
    page_index: int
    region_count: int
    regions: list[NormalizedRegion]


class SourceOutput(TypedDict):
    path: str | None
    type: str


class ArtifactsOutput(TypedDict):
    parsed_dir: str
    markdown_path: str
    regions_path: str
    model_path: str | None
    pipeline_output_path: str


class StatsOutput(TypedDict):
    page_count: int
    region_count: int
    label_counts: dict[str, int]


class ContentOutput(TypedDict):
    markdown: str
    text_regions: list[NormalizedRegion]
    table_regions: list[NormalizedRegion]
    formula_regions: list[NormalizedRegion]
    image_regions: list[NormalizedRegion]


class PipelineOutput(TypedDict):
    schema_version: str
    ok: bool
    document_id: str
    source: SourceOutput
    artifacts: ArtifactsOutput
    stats: StatsOutput
    content: ContentOutput
    pages: list[PageOutput]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize glmocr saved artifacts into a pipeline-safe JSON contract."
    )
    parser.add_argument(
        "--parsed-dir",
        required=True,
        help="Directory created by glmocr for one document, such as ./output/paper",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Original input file path. If omitted, the script tries to infer it.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Target JSON path. Defaults to <parsed-dir>/pipeline-output.json",
    )
    return parser.parse_args()


def resolve_artifact_path(parsed_dir: Path, exact_name: str, glob_pattern: str) -> Path:
    exact_path = parsed_dir / exact_name
    if exact_path.exists():
        return exact_path

    matches = sorted(parsed_dir.glob(glob_pattern))
    if len(matches) == 1:
        return matches[0]

    raise FileNotFoundError(
        f"Could not resolve artifact {exact_name!r} inside {parsed_dir}."
    )


def discover_source_path(parsed_dir: Path, stem: str, source_arg: str | None) -> Path | None:
    if source_arg:
        return Path(source_arg).expanduser().resolve()

    candidate_dirs = [
        parsed_dir.parent.parent,
        parsed_dir.parent,
        parsed_dir,
    ]
    candidate_suffixes = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"]

    for candidate_dir in candidate_dirs:
        for suffix in candidate_suffixes:
            candidate_path = candidate_dir / f"{stem}{suffix}"
            if candidate_path.exists():
                return candidate_path.resolve()

    return None


def detect_source_type(source_path: Path | None) -> str:
    if source_path is None:
        return "unknown"

    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        return "image"
    return "unknown"


def load_regions(regions_path: Path) -> list[list[RawJson]]:
    raw_data = json.loads(regions_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, list):
        raise ValueError(f"Expected a list of pages in {regions_path}.")

    pages: list[list[RawJson]] = []
    for raw_page in raw_data:
        if not isinstance(raw_page, list):
            raise ValueError(f"Expected every page to be a list in {regions_path}.")

        page_regions: list[RawJson] = []
        for raw_region in raw_page:
            if not isinstance(raw_region, dict):
                raise ValueError(
                    f"Expected every region to be an object in {regions_path}."
                )
            page_regions.append(cast(RawJson, raw_region))
        pages.append(page_regions)

    return pages


def normalize_bbox(raw_bbox: Any) -> list[int] | None:
    if not isinstance(raw_bbox, list):
        return None

    normalized_bbox: list[int] = []
    for value in raw_bbox:
        if not isinstance(value, (int, float)):
            return None
        normalized_bbox.append(int(value))
    return normalized_bbox


def normalize_region(page_index: int, raw_region: RawJson) -> NormalizedRegion:
    raw_index = raw_region.get("index")
    raw_label = raw_region.get("label")
    raw_native_label = raw_region.get("native_label")
    raw_content = raw_region.get("content")

    region_index = raw_index if isinstance(raw_index, int) else -1
    label = raw_label if isinstance(raw_label, str) else "unknown"
    native_label = raw_native_label if isinstance(raw_native_label, str) else None
    content = raw_content if isinstance(raw_content, str) else None

    return {
        "page_index": page_index,
        "region_index": region_index,
        "label": label,
        "native_label": native_label,
        "bbox_2d": normalize_bbox(raw_region.get("bbox_2d")),
        "content": content,
    }


def classify_region(region: NormalizedRegion) -> str:
    label = region["label"].lower()
    native_label = (region["native_label"] or "").lower()
    combined = f"{label} {native_label}"

    if "table" in combined:
        return "table"
    if "formula" in combined:
        return "formula"
    if "image" in combined or "chart" in combined or label == "image":
        return "image"
    return "text"


def build_pipeline_output(
    parsed_dir: Path,
    output_path: Path,
    source_path: Path | None,
) -> PipelineOutput:
    stem = parsed_dir.name
    markdown_path = resolve_artifact_path(parsed_dir, f"{stem}.md", "*.md")
    regions_path = resolve_artifact_path(parsed_dir, f"{stem}.json", "*.json")
    model_path = parsed_dir / f"{stem}_model.json"

    markdown = markdown_path.read_text(encoding="utf-8")
    raw_pages = load_regions(regions_path)

    label_counter: Counter[str] = Counter()
    page_outputs: list[PageOutput] = []
    text_regions: list[NormalizedRegion] = []
    table_regions: list[NormalizedRegion] = []
    formula_regions: list[NormalizedRegion] = []
    image_regions: list[NormalizedRegion] = []

    for page_index, raw_page in enumerate(raw_pages):
        normalized_regions: list[NormalizedRegion] = []
        for raw_region in raw_page:
            normalized_region = normalize_region(page_index, raw_region)
            normalized_regions.append(normalized_region)
            label_counter[normalized_region["label"]] += 1

            category = classify_region(normalized_region)
            if category == "table":
                table_regions.append(normalized_region)
            elif category == "formula":
                formula_regions.append(normalized_region)
            elif category == "image":
                image_regions.append(normalized_region)
            else:
                text_regions.append(normalized_region)

        page_outputs.append(
            {
                "page_index": page_index,
                "region_count": len(normalized_regions),
                "regions": normalized_regions,
            }
        )

    return {
        "schema_version": "use-ocr.v1",
        "ok": True,
        "document_id": stem,
        "source": {
            "path": str(source_path) if source_path is not None else None,
            "type": detect_source_type(source_path),
        },
        "artifacts": {
            "parsed_dir": str(parsed_dir),
            "markdown_path": str(markdown_path),
            "regions_path": str(regions_path),
            "model_path": str(model_path) if model_path.exists() else None,
            "pipeline_output_path": str(output_path),
        },
        "stats": {
            "page_count": len(page_outputs),
            "region_count": sum(page["region_count"] for page in page_outputs),
            "label_counts": dict(sorted(label_counter.items())),
        },
        "content": {
            "markdown": markdown,
            "text_regions": text_regions,
            "table_regions": table_regions,
            "formula_regions": formula_regions,
            "image_regions": image_regions,
        },
        "pages": page_outputs,
    }


def main() -> int:
    args = parse_args()
    parsed_dir = Path(args.parsed_dir).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else parsed_dir / "pipeline-output.json"
    )

    if not parsed_dir.exists():
        raise FileNotFoundError(f"Parsed directory not found: {parsed_dir}")

    source_path = discover_source_path(parsed_dir, parsed_dir.name, args.source)
    pipeline_output = build_pipeline_output(parsed_dir, output_path, source_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(pipeline_output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
