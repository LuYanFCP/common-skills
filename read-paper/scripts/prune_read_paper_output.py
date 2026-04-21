#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Final


OBSIDIAN_EMBED_RE: Final[re.Pattern[str]] = re.compile(r"!\[\[([^\]]+)\]\]")
MARKDOWN_IMAGE_RE: Final[re.Pattern[str]] = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prune read-paper outputs for Obsidian so only report.md "
            "and images referenced by the report remain."
        )
    )
    parser.add_argument(
        "output_roots",
        nargs="+",
        help="One or more read-paper output directories to prune.",
    )
    return parser.parse_args()


def normalize_embedded_path(raw_path: str) -> str:
    path = raw_path.strip()
    path = path.split("|", 1)[0]
    path = path.split("#", 1)[0]
    return path.strip()


def collect_referenced_assets(report_text: str) -> set[str]:
    referenced_assets: set[str] = set()

    for match in OBSIDIAN_EMBED_RE.finditer(report_text):
        embedded_path = normalize_embedded_path(match.group(1))
        if embedded_path.startswith("assets/"):
            referenced_assets.add(embedded_path)

    for match in MARKDOWN_IMAGE_RE.finditer(report_text):
        image_path = normalize_embedded_path(match.group(1))
        if image_path.startswith("assets/"):
            referenced_assets.add(image_path)

    return referenced_assets


def prune_assets_dir(assets_dir: Path, referenced_assets: set[str]) -> list[str]:
    deleted_paths: list[str] = []
    if not assets_dir.exists():
        return deleted_paths

    for child in assets_dir.iterdir():
        relative_asset_path = f"assets/{child.name}"
        if child.is_dir():
            shutil.rmtree(child)
            deleted_paths.append(str(child))
            continue
        if relative_asset_path not in referenced_assets:
            child.unlink()
            deleted_paths.append(str(child))

    if not any(assets_dir.iterdir()):
        assets_dir.rmdir()
        deleted_paths.append(str(assets_dir))

    return deleted_paths


def prune_output_root(output_root: Path) -> list[str]:
    report_path = output_root / "report.md"
    if not report_path.exists():
        raise FileNotFoundError(f"report.md not found in {output_root}")

    report_text = report_path.read_text(encoding="utf-8")
    referenced_assets = collect_referenced_assets(report_text)

    deleted_paths = prune_assets_dir(output_root / "assets", referenced_assets)

    keep_names: set[str] = {"report.md"}
    if (output_root / "assets").exists():
        keep_names.add("assets")

    for child in output_root.iterdir():
        if child.name in keep_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        deleted_paths.append(str(child))

    return deleted_paths


def main() -> int:
    args = parse_args()

    for raw_output_root in args.output_roots:
        output_root = Path(raw_output_root).expanduser().resolve()
        deleted_paths = prune_output_root(output_root)
        print(f"[pruned] {output_root}")
        if deleted_paths:
            for deleted_path in deleted_paths:
                print(f"  removed: {deleted_path}")
        else:
            print("  removed: nothing")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
