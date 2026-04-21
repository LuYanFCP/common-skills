---
name: read-paper
description: Download arXiv papers, parse them with local `use-ocr`, and write Obsidian Markdown paper reports with frontmatter metadata and copied figure assets. Use when the user types `/read-paper <arxiv>`, asks to read or summarize an arXiv paper, or wants a paper note/report for Obsidian.
---

# read-paper

Use this skill for arXiv-first paper reading. This workflow standardizes three steps:

1. Download the paper PDF from arXiv.
2. Parse the PDF with the local OCR flow from `use-ocr`.
3. Generate an Obsidian-ready report note with frontmatter metadata and copied figure assets.

## When to use

- User types `/read-paper <arxiv-id-or-url>`.
- User asks to read, summarize, or organize an arXiv paper.
- User wants an Obsidian note for a paper with metadata and local figure embeds.

## Input contract

1. Accept either:
   - an arXiv ID like `2503.01840`
   - an arXiv URL like `https://arxiv.org/abs/2503.01840`
2. If the input is not an arXiv ID or URL, stop and ask whether the workflow should be adapted to a local PDF instead.
3. Default output root: `{baseDir}/output/arxiv-<normalized-id>/`
4. If the user explicitly provides an output directory, pass it with `--output`.

## Standard workflow

1. Run the wrapper:

   ```bash
   python3.12 {baseDir}/scripts/run_read_paper.py <arxiv-id-or-url>
   ```

2. If the user wants a specific output directory:

   ```bash
   python3.12 {baseDir}/scripts/run_read_paper.py <arxiv-id-or-url> --output <dir>
   ```

3. Read `read-paper-summary.json` from the output root to discover the generated paths.
4. Read `paper-context.json` and the OCR markdown file.
5. Update `report.md` in place:
   - keep the YAML frontmatter
   - replace the placeholder analysis sections with a complete report
   - do not keep or add an `OCR Outline` section unless the user explicitly asks for it
6. Prune the output root so the final vault-friendly deliverable keeps only:
   - `report.md`
   - the images actually referenced by `report.md`
   - recommended command:

   ```bash
   python3.12 {baseDir}/scripts/prune_read_paper_output.py <output-root>
   ```

## Report requirements

- Keep YAML frontmatter with these fields:
  - `title`
  - `aliases`
  - `authors`
  - `published`
  - `year`
  - `source`
  - `note_type`
  - `arxiv_id`
  - `arxiv_url`
  - `pdf_url`
  - `primary_category`
  - `categories`
  - `tags`
  - `status`
  - `reading_stage`
  - `citation_key`
  - `assets_dir`
- Write the note as Obsidian Markdown.
- Use `![[assets/...]]` for copied figure embeds.
- Keep source paths relative when possible.
- Use OCR headings only as internal context; do not mirror them into a standalone `OCR Outline` section in the final report unless the user explicitly requests it.
- Do not leave links or frontmatter fields pointing to transient OCR artifacts that will not exist in the final Obsidian folder.
- Do not invent claims that are not supported by the paper.
- If OCR is ambiguous, say so explicitly in the report.

## Minimum sections

- `## Snapshot`
- `## Abstract`
- `## Reading Report`
- `### TL;DR`
- `### Problem`
- `### Core Idea`
- `### Method`
- `### Experiments`
- `### Strengths`
- `### Limitations`
- `### Open Questions`
- `## Figure Appendix`

## Figure handling

- Use `paper-context.json` figure metadata to locate copied assets.
- Prefer embedding only the most relevant figures inside `Reading Report`.
- Keep the full copied figure list in `Figure Appendix`.
- If an OCR image is missing, note it instead of removing the reference silently.
- After the report is finalized, remove unreferenced images from `assets/`.

## Output rules

- Mention the output root, `report.md`, the retained image assets, and the arXiv URL in the final response.
- If download or OCR fails, show the exact error and stop.

## Additional resources

- Report template and output contract: [reference.md](reference.md)
- Workflow wrapper: `scripts/run_read_paper.py`
- Output pruner: `scripts/prune_read_paper_output.py`
