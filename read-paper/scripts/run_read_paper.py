#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ATOM_NAMESPACES: dict[str, str] = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

ARXIV_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?arxiv\.org/"
    r"(?P<kind>abs|pdf)/(?P<identifier>[^?#]+?)(?:\.pdf)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
ARXIV_ID_RE = re.compile(r"^[A-Za-z0-9._/\-]+(?:v\d+)?$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
FIGURE_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s+([A-Za-z0-9.\-]+)\s*[:.]\s*(.+)$")
USER_AGENT = "common-skills/read-paper"
DEFAULT_REPORT_NAME = "report.md"
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "via",
    "with",
}


class SectionHeadingJson(TypedDict):
    level: int
    title: str


class FigureBlockJson(TypedDict):
    label: str
    caption: str
    source_images: list[str]
    asset_images: list[str]
    missing_source_images: list[str]


class PaperContextJson(TypedDict):
    schema_version: str
    paper_ref: dict[str, Any]
    metadata: dict[str, Any]
    artifacts: dict[str, Any]
    ocr: dict[str, Any]
    sections: list[SectionHeadingJson]
    figures: list[FigureBlockJson]


class RunSummaryJson(TypedDict):
    schema_version: str
    ok: bool
    paper_ref: dict[str, str]
    output_root: str
    pdf_path: str
    metadata_path: str
    paper_context_path: str
    read_paper_summary_path: str
    report_path: str
    ocr_output_root: str
    ocr_run_summary_path: str
    pipeline_output_path: str
    markdown_path: str
    assets_dir: str
    figure_count: int
    section_count: int


@dataclass(frozen=True, slots=True)
class ArxivRef:
    raw_input: str
    arxiv_id: str
    slug: str
    abs_url: str
    pdf_url: str


@dataclass(frozen=True, slots=True)
class PaperMetadata:
    entry_id: str
    title: str
    summary: str
    authors: list[str]
    published: str | None
    updated: str | None
    primary_category: str | None
    categories: list[str]
    comment: str | None
    journal_ref: str | None
    doi: str | None
    pdf_url: str


@dataclass(frozen=True, slots=True)
class SectionHeading:
    level: int
    title: str


@dataclass(slots=True)
class FigureBlock:
    label: str
    caption: str
    source_images: list[str]
    asset_images: list[str]
    missing_source_images: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download an arXiv paper, run the local OCR workflow, copy figure assets, "
            "and generate an Obsidian report scaffold."
        )
    )
    parser.add_argument("paper_ref", help="arXiv ID or arXiv URL")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output root directory. Defaults to {baseDir}/output/arxiv-<normalized-id> "
            "inside the skill directory."
        ),
    )
    parser.add_argument(
        "--skip-ocr-setup",
        action="store_true",
        help="Skip the OCR setup step when the local OCR environment is already ready.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the PDF even when paper.pdf already exists.",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Re-run OCR even when ocr/run-summary.json already exists.",
    )
    return parser.parse_args()


def skill_base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def use_ocr_runner_path() -> Path:
    return repo_root() / "use-ocr" / "scripts" / "run_use_ocr.py"


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "paper"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def strip_optional_pdf_suffix(identifier: str) -> str:
    if identifier.lower().endswith(".pdf"):
        return identifier[:-4]
    return identifier


def normalize_arxiv_id(raw_input: str) -> str:
    candidate = raw_input.strip()
    if not candidate:
        raise ValueError("arXiv reference cannot be empty.")

    if candidate.lower().startswith("arxiv:"):
        candidate = candidate.split(":", 1)[1]

    url_match = ARXIV_URL_RE.fullmatch(candidate)
    if url_match is not None:
        candidate = url_match.group("identifier")

    candidate = strip_optional_pdf_suffix(candidate).strip("/")
    if not ARXIV_ID_RE.fullmatch(candidate):
        raise ValueError(f"Unsupported arXiv reference: {raw_input}")
    return candidate


def build_arxiv_ref(raw_input: str) -> ArxivRef:
    arxiv_id = normalize_arxiv_id(raw_input)
    quoted_id = quote(arxiv_id, safe="/.")
    return ArxivRef(
        raw_input=raw_input,
        arxiv_id=arxiv_id,
        slug=slugify(arxiv_id),
        abs_url=f"https://arxiv.org/abs/{quoted_id}",
        pdf_url=f"https://arxiv.org/pdf/{quoted_id}.pdf",
    )


def resolve_output_root(output_arg: str | None, paper_ref: ArxivRef) -> Path:
    if output_arg is None:
        return (skill_base_dir() / "output" / f"arxiv-{paper_ref.slug}").resolve()
    return Path(output_arg).expanduser().resolve()


def http_get_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=120) as response:
        return response.read()


def http_get_text(url: str) -> str:
    return http_get_bytes(url).decode("utf-8")


def optional_xml_text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    value = normalize_whitespace(node.text)
    return value or None


def fetch_arxiv_metadata(paper_ref: ArxivRef) -> PaperMetadata:
    query_url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=id:{quote(paper_ref.arxiv_id, safe='/.')}&max_results=1"
    )

    try:
        xml_text = http_get_text(query_url)
    except HTTPError as exc:
        raise RuntimeError(f"Failed to fetch arXiv metadata: HTTP {exc.code} for {query_url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to fetch arXiv metadata: {exc.reason}") from exc

    root = ET.fromstring(xml_text)
    entry = root.find("atom:entry", ATOM_NAMESPACES)
    if entry is None:
        raise RuntimeError(f"No arXiv entry found for {paper_ref.arxiv_id}")

    title = normalize_whitespace(entry.findtext("atom:title", default="", namespaces=ATOM_NAMESPACES))
    summary = normalize_whitespace(
        entry.findtext("atom:summary", default="", namespaces=ATOM_NAMESPACES)
    )
    authors = [
        normalize_whitespace(author.findtext("atom:name", default="", namespaces=ATOM_NAMESPACES))
        for author in entry.findall("atom:author", ATOM_NAMESPACES)
        if normalize_whitespace(
            author.findtext("atom:name", default="", namespaces=ATOM_NAMESPACES)
        )
    ]
    entry_id = normalize_whitespace(
        entry.findtext("atom:id", default=paper_ref.abs_url, namespaces=ATOM_NAMESPACES)
    )
    published_raw = normalize_whitespace(
        entry.findtext("atom:published", default="", namespaces=ATOM_NAMESPACES)
    )
    updated_raw = normalize_whitespace(
        entry.findtext("atom:updated", default="", namespaces=ATOM_NAMESPACES)
    )
    published = published_raw[:10] if published_raw else None
    updated = updated_raw[:10] if updated_raw else None

    primary_category_node = entry.find("arxiv:primary_category", ATOM_NAMESPACES)
    primary_category = None
    if primary_category_node is not None:
        primary_category = normalize_whitespace(primary_category_node.attrib.get("term", ""))
        primary_category = primary_category or None

    categories: list[str] = []
    for category_node in entry.findall("atom:category", ATOM_NAMESPACES):
        category_value = normalize_whitespace(category_node.attrib.get("term", ""))
        if category_value and category_value not in categories:
            categories.append(category_value)

    comment = optional_xml_text(entry.find("arxiv:comment", ATOM_NAMESPACES))
    journal_ref = optional_xml_text(entry.find("arxiv:journal_ref", ATOM_NAMESPACES))
    doi = optional_xml_text(entry.find("arxiv:doi", ATOM_NAMESPACES))

    pdf_url = paper_ref.pdf_url
    for link_node in entry.findall("atom:link", ATOM_NAMESPACES):
        href = normalize_whitespace(link_node.attrib.get("href", ""))
        title_attr = normalize_whitespace(link_node.attrib.get("title", ""))
        if not href:
            continue
        if title_attr == "pdf" or href.endswith(".pdf"):
            pdf_url = href
            break

    return PaperMetadata(
        entry_id=entry_id,
        title=title or paper_ref.arxiv_id,
        summary=summary,
        authors=authors,
        published=published,
        updated=updated,
        primary_category=primary_category,
        categories=categories,
        comment=comment,
        journal_ref=journal_ref,
        doi=doi,
        pdf_url=pdf_url,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def download_pdf(pdf_url: str, target_path: Path, force_download: bool) -> None:
    if target_path.exists() and not force_download:
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(f"{target_path.suffix}.part")

    try:
        payload = http_get_bytes(pdf_url)
    except HTTPError as exc:
        raise RuntimeError(f"Failed to download PDF: HTTP {exc.code} for {pdf_url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to download PDF: {exc.reason}") from exc

    temporary_path.write_bytes(payload)
    temporary_path.replace(target_path)


def run_use_ocr(pdf_path: Path, output_root: Path, skip_ocr_setup: bool, force_ocr: bool) -> dict[str, Any]:
    run_summary_path = output_root / "run-summary.json"
    if force_ocr and output_root.exists():
        shutil.rmtree(output_root)

    if run_summary_path.exists():
        return read_json(run_summary_path)

    command: list[str] = [
        sys.executable,
        str(use_ocr_runner_path()),
        str(pdf_path),
        "--output",
        str(output_root),
    ]
    if skip_ocr_setup:
        command.append("--skip-setup")

    subprocess.run(command, check=True)
    if not run_summary_path.exists():
        raise FileNotFoundError(f"OCR run summary not found: {run_summary_path}")
    return read_json(run_summary_path)


def extract_headings(markdown_text: str) -> list[SectionHeading]:
    headings: list[SectionHeading] = []
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        match = HEADING_RE.match(line)
        if match is None:
            continue
        headings.append(SectionHeading(level=len(match.group(1)), title=match.group(2).strip()))
    return headings


def extract_figure_blocks(markdown_text: str) -> list[FigureBlock]:
    figure_blocks: list[FigureBlock] = []
    pending_images: list[str] = []
    uncaptioned_index = 0

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        image_match = IMAGE_LINE_RE.match(line)
        if image_match is not None:
            pending_images.append(image_match.group(1))
            continue

        caption_match = FIGURE_CAPTION_RE.match(line)
        if caption_match is not None and pending_images:
            label_prefix = caption_match.group(1)
            label = f"{label_prefix} {caption_match.group(2)}"
            figure_blocks.append(
                FigureBlock(
                    label=label,
                    caption=line,
                    source_images=pending_images.copy(),
                    asset_images=[],
                    missing_source_images=[],
                )
            )
            pending_images.clear()
            continue

        if pending_images:
            uncaptioned_index += 1
            figure_blocks.append(
                FigureBlock(
                    label=f"Image Block {uncaptioned_index}",
                    caption="Uncaptioned image block extracted from OCR markdown.",
                    source_images=pending_images.copy(),
                    asset_images=[],
                    missing_source_images=[],
                )
            )
            pending_images.clear()

    if pending_images:
        uncaptioned_index += 1
        figure_blocks.append(
            FigureBlock(
                label=f"Image Block {uncaptioned_index}",
                caption="Uncaptioned image block extracted from OCR markdown.",
                source_images=pending_images.copy(),
                asset_images=[],
                missing_source_images=[],
            )
        )

    return figure_blocks


def copy_figure_assets(parsed_dir: Path, assets_dir: Path, figure_blocks: list[FigureBlock]) -> None:
    if not figure_blocks:
        return

    assets_dir.mkdir(parents=True, exist_ok=True)
    used_filenames: set[str] = set()

    for figure_index, figure in enumerate(figure_blocks, start=1):
        for image_index, source_image in enumerate(figure.source_images, start=1):
            source_path = (parsed_dir / source_image).resolve()
            if not source_path.exists():
                figure.missing_source_images.append(source_image)
                continue

            suffix = source_path.suffix.lower() or ".png"
            target_name = f"figure-{figure_index:03d}-{image_index:02d}{suffix}"
            while target_name in used_filenames:
                target_name = f"figure-{figure_index:03d}-{image_index:02d}-{len(used_filenames):02d}{suffix}"

            target_path = assets_dir / target_name
            shutil.copy2(source_path, target_path)
            used_filenames.add(target_name)
            figure.asset_images.append(f"assets/{target_name}")


def build_citation_key(metadata: PaperMetadata) -> str:
    if metadata.authors:
        author_tokens = re.findall(r"[A-Za-z0-9]+", metadata.authors[0].lower())
        author_root = author_tokens[-1] if author_tokens else "paper"
    else:
        author_root = "paper"

    year = metadata.published[:4] if metadata.published else "undated"
    title_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", metadata.title.lower())
        if token not in STOPWORDS
    ]
    title_root = title_tokens[0] if title_tokens else "paper"
    return f"{author_root}{year}{title_root}"


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def append_yaml_scalar(lines: list[str], key: str, value: str | int | None) -> None:
    if value is None:
        lines.append(f"{key}: null")
        return
    if isinstance(value, int):
        lines.append(f"{key}: {value}")
        return
    lines.append(f"{key}: {yaml_quote(value)}")


def append_yaml_list(lines: list[str], key: str, values: list[str]) -> None:
    if not values:
        lines.append(f"{key}: []")
        return
    lines.append(f"{key}:")
    for value in values:
        lines.append(f"  - {yaml_quote(value)}")


def build_frontmatter(
    metadata: PaperMetadata,
    paper_ref: ArxivRef,
    citation_key: str,
    tags: list[str],
) -> str:
    created_at = datetime.now(UTC).date().isoformat()
    aliases = [f"{paper_ref.arxiv_id}", f"{metadata.title} ({paper_ref.arxiv_id})"]
    lines: list[str] = ["---"]
    append_yaml_scalar(lines, "title", metadata.title)
    append_yaml_list(lines, "aliases", aliases)
    append_yaml_list(lines, "authors", metadata.authors)
    append_yaml_scalar(lines, "published", metadata.published)
    append_yaml_scalar(lines, "updated", metadata.updated)
    append_yaml_scalar(lines, "year", int(metadata.published[:4]) if metadata.published else None)
    append_yaml_scalar(lines, "source", "arxiv")
    append_yaml_scalar(lines, "note_type", "paper-report")
    append_yaml_scalar(lines, "arxiv_id", paper_ref.arxiv_id)
    append_yaml_scalar(lines, "arxiv_url", paper_ref.abs_url)
    append_yaml_scalar(lines, "pdf_url", metadata.pdf_url)
    append_yaml_scalar(lines, "primary_category", metadata.primary_category)
    append_yaml_list(lines, "categories", metadata.categories)
    append_yaml_scalar(lines, "doi", metadata.doi)
    append_yaml_scalar(lines, "journal_ref", metadata.journal_ref)
    append_yaml_scalar(lines, "comment", metadata.comment)
    append_yaml_list(lines, "tags", tags)
    append_yaml_scalar(lines, "status", "draft")
    append_yaml_scalar(lines, "reading_stage", "ocr-parsed")
    append_yaml_scalar(lines, "created", created_at)
    append_yaml_scalar(lines, "updated_note", created_at)
    append_yaml_scalar(lines, "citation_key", citation_key)
    append_yaml_scalar(lines, "assets_dir", "assets")
    lines.append("---")
    return "\n".join(lines)


def build_report_markdown(
    metadata: PaperMetadata,
    paper_ref: ArxivRef,
    citation_key: str,
    figures: list[FigureBlock],
) -> str:
    tags = ["paper", "arxiv", f"arxiv/{paper_ref.slug}"]
    if metadata.primary_category:
        tags.append(f"category/{slugify(metadata.primary_category)}")

    lines: list[str] = [
        build_frontmatter(
            metadata=metadata,
            paper_ref=paper_ref,
            citation_key=citation_key,
            tags=tags,
        ),
        "",
        f"# {metadata.title}",
        "",
        "## Snapshot",
        "",
        f"- Authors: {', '.join(metadata.authors) if metadata.authors else 'Unknown'}",
        f"- Published: {metadata.published or 'Unknown'}",
        f"- Categories: {', '.join(metadata.categories) if metadata.categories else 'Unknown'}",
        f"- arXiv: [{paper_ref.arxiv_id}]({paper_ref.abs_url})",
        "",
        "## Abstract",
        "",
        metadata.summary or "_Abstract unavailable from arXiv metadata._",
        "",
        "## Reading Report",
        "",
        "### TL;DR",
        "",
        "_Replace with a 3-5 sentence summary of the paper._",
        "",
        "### Problem",
        "",
        "_What problem does the paper solve, and why does it matter?_",
        "",
        "### Core Idea",
        "",
        "_What is the paper's main insight or contribution?_",
        "",
        "### Method",
        "",
        "_Describe the method, architecture, data, and training or inference pipeline._",
        "",
        "### Experiments",
        "",
        "_Summarize datasets, baselines, metrics, and the most important results._",
        "",
        "### Strengths",
        "",
        "- _Strength 1_",
        "- _Strength 2_",
        "",
        "### Limitations",
        "",
        "- _Limitation 1_",
        "- _Limitation 2_",
        "",
        "### Open Questions",
        "",
        "- _Question 1_",
        "- _Question 2_",
        "",
        "## Figure Appendix",
        "",
    ]

    if figures:
        for figure in figures:
            lines.append(f"### {figure.label}")
            lines.append("")
            lines.append(figure.caption)
            lines.append("")
            if figure.asset_images:
                for asset_image in figure.asset_images:
                    lines.append(f"![[{asset_image}]]")
                    lines.append("")
            if figure.missing_source_images:
                missing_list = ", ".join(figure.missing_source_images)
                lines.append(f"- Missing source images: {missing_list}")
                lines.append("")
    else:
        lines.append("_No figures were extracted from OCR markdown._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def figure_block_to_json(figure: FigureBlock) -> FigureBlockJson:
    return {
        "label": figure.label,
        "caption": figure.caption,
        "source_images": figure.source_images,
        "asset_images": figure.asset_images,
        "missing_source_images": figure.missing_source_images,
    }


def section_heading_to_json(heading: SectionHeading) -> SectionHeadingJson:
    return {"level": heading.level, "title": heading.title}


def build_context_payload(
    metadata: PaperMetadata,
    paper_ref: ArxivRef,
    output_root: Path,
    pdf_path: Path,
    metadata_path: Path,
    report_path: Path,
    paper_context_path: Path,
    run_summary_path: Path,
    markdown_path: Path,
    pipeline_output_path: Path,
    parsed_dir: Path,
    figures: list[FigureBlock],
    headings: list[SectionHeading],
    pipeline_output: dict[str, Any],
) -> PaperContextJson:
    stats = pipeline_output.get("stats", {})
    return {
        "schema_version": "read-paper.v1",
        "paper_ref": {
            "raw_input": paper_ref.raw_input,
            "arxiv_id": paper_ref.arxiv_id,
            "slug": paper_ref.slug,
            "abs_url": paper_ref.abs_url,
            "pdf_url": paper_ref.pdf_url,
        },
        "metadata": asdict(metadata),
        "artifacts": {
            "output_root": str(output_root),
            "pdf_path": str(pdf_path),
            "metadata_path": str(metadata_path),
            "report_path": str(report_path),
            "paper_context_path": str(paper_context_path),
            "assets_dir": str(output_root / "assets"),
        },
        "ocr": {
            "run_summary_path": str(run_summary_path),
            "parsed_dir": str(parsed_dir),
            "markdown_path": str(markdown_path),
            "pipeline_output_path": str(pipeline_output_path),
            "page_count": stats.get("page_count"),
            "region_count": stats.get("region_count"),
            "label_counts": stats.get("label_counts", {}),
        },
        "sections": [section_heading_to_json(heading) for heading in headings],
        "figures": [figure_block_to_json(figure) for figure in figures],
    }


def build_run_summary_payload(
    paper_ref: ArxivRef,
    output_root: Path,
    pdf_path: Path,
    metadata_path: Path,
    paper_context_path: Path,
    read_paper_summary_path: Path,
    report_path: Path,
    ocr_output_root: Path,
    ocr_run_summary_path: Path,
    pipeline_output_path: Path,
    markdown_path: Path,
    assets_dir: Path,
    figures: list[FigureBlock],
    headings: list[SectionHeading],
) -> RunSummaryJson:
    return {
        "schema_version": "read-paper.v1",
        "ok": True,
        "paper_ref": {
            "raw_input": paper_ref.raw_input,
            "arxiv_id": paper_ref.arxiv_id,
            "abs_url": paper_ref.abs_url,
            "pdf_url": paper_ref.pdf_url,
        },
        "output_root": str(output_root),
        "pdf_path": str(pdf_path),
        "metadata_path": str(metadata_path),
        "paper_context_path": str(paper_context_path),
        "read_paper_summary_path": str(read_paper_summary_path),
        "report_path": str(report_path),
        "ocr_output_root": str(ocr_output_root),
        "ocr_run_summary_path": str(ocr_run_summary_path),
        "pipeline_output_path": str(pipeline_output_path),
        "markdown_path": str(markdown_path),
        "assets_dir": str(assets_dir),
        "figure_count": len(figures),
        "section_count": len(headings),
    }


def main() -> int:
    args = parse_args()

    paper_ref = build_arxiv_ref(args.paper_ref)
    output_root = resolve_output_root(args.output, paper_ref)
    output_root.mkdir(parents=True, exist_ok=True)

    metadata = fetch_arxiv_metadata(paper_ref)
    metadata_path = output_root / "metadata.json"
    write_json(metadata_path, asdict(metadata))

    pdf_path = output_root / "paper.pdf"
    download_pdf(metadata.pdf_url, pdf_path, force_download=args.force_download)

    ocr_output_root = output_root / "ocr"
    ocr_run_summary = run_use_ocr(
        pdf_path=pdf_path,
        output_root=ocr_output_root,
        skip_ocr_setup=args.skip_ocr_setup,
        force_ocr=args.force_ocr,
    )
    ocr_run_summary_path = ocr_output_root / "run-summary.json"

    documents = ocr_run_summary.get("documents", [])
    if not documents:
        raise RuntimeError(f"No OCR documents found in {ocr_run_summary_path}")

    primary_document = documents[0]
    parsed_dir = Path(str(primary_document["parsed_dir"]))
    pipeline_output_path = Path(str(primary_document["pipeline_output_path"]))
    pipeline_output = read_json(pipeline_output_path)

    artifacts = pipeline_output.get("artifacts", {})
    markdown_path = Path(str(artifacts.get("markdown_path", parsed_dir / "paper.md")))
    if not markdown_path.exists():
        raise FileNotFoundError(f"OCR markdown not found: {markdown_path}")
    markdown_text = markdown_path.read_text(encoding="utf-8")

    headings = extract_headings(markdown_text)
    figures = extract_figure_blocks(markdown_text)

    assets_dir = output_root / "assets"
    copy_figure_assets(parsed_dir=parsed_dir, assets_dir=assets_dir, figure_blocks=figures)

    citation_key = build_citation_key(metadata)
    report_path = output_root / DEFAULT_REPORT_NAME
    report_markdown = build_report_markdown(
        metadata=metadata,
        paper_ref=paper_ref,
        citation_key=citation_key,
        figures=figures,
    )
    report_path.write_text(report_markdown, encoding="utf-8")

    paper_context_path = output_root / "paper-context.json"
    paper_context_payload = build_context_payload(
        metadata=metadata,
        paper_ref=paper_ref,
        output_root=output_root,
        pdf_path=pdf_path,
        metadata_path=metadata_path,
        report_path=report_path,
        paper_context_path=paper_context_path,
        run_summary_path=ocr_run_summary_path,
        markdown_path=markdown_path,
        pipeline_output_path=pipeline_output_path,
        parsed_dir=parsed_dir,
        figures=figures,
        headings=headings,
        pipeline_output=pipeline_output,
    )
    write_json(paper_context_path, paper_context_payload)

    read_paper_summary_path = output_root / "read-paper-summary.json"
    run_summary_payload = build_run_summary_payload(
        paper_ref=paper_ref,
        output_root=output_root,
        pdf_path=pdf_path,
        metadata_path=metadata_path,
        paper_context_path=paper_context_path,
        read_paper_summary_path=read_paper_summary_path,
        report_path=report_path,
        ocr_output_root=ocr_output_root,
        ocr_run_summary_path=ocr_run_summary_path,
        pipeline_output_path=pipeline_output_path,
        markdown_path=markdown_path,
        assets_dir=assets_dir,
        figures=figures,
        headings=headings,
    )
    write_json(read_paper_summary_path, run_summary_payload)

    json.dump(run_summary_payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
