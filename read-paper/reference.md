# read-paper reference

## Command

Default output root:

```bash
python3.12 {baseDir}/scripts/run_read_paper.py 2503.01840
```

Explicit output root:

```bash
python3.12 {baseDir}/scripts/run_read_paper.py 2503.01840 --output ./notes/arxiv-2503-01840
```

Useful flags:

- `--skip-ocr-setup`: Skip the OCR setup step when the local OCR stack is already installed.
- `--force-download`: Re-download `paper.pdf`.
- `--force-ocr`: Re-run OCR even when `ocr/run-summary.json` already exists.

Prune the final output for Obsidian:

```bash
python3.12 {baseDir}/scripts/prune_read_paper_output.py ./output/arxiv-2503-01840
```

## Output tree

The wrapper creates transient working artifacts during report generation:

```text
output/arxiv-2503-01840/
├── assets/
├── metadata.json
├── ocr/
│   ├── run-summary.json
│   └── parsed/
│       └── paper/
│           ├── paper.md
│           ├── paper.json
│           ├── paper_model.json
│           └── pipeline-output.json
├── paper-context.json
├── paper.pdf
├── read-paper-summary.json
└── report.md
```

After the report is finalized, prune the output root for Obsidian so it keeps only:

```text
output/arxiv-2503-01840/
├── assets/
└── report.md
```

## Generated files

- `metadata.json`: arXiv API metadata used for frontmatter.
- `paper.pdf`: downloaded source PDF.
- `ocr/run-summary.json`: `use-ocr` run summary.
- `ocr/parsed/paper/paper.md`: OCR markdown for the full paper.
- `ocr/parsed/paper/pipeline-output.json`: normalized OCR contract.
- `paper-context.json`: compact context for the report-writing step.
- `report.md`: Obsidian report scaffold that should be filled in by the agent.
- `assets/`: copied figure crops referenced by the report.

## paper-context.json

`paper-context.json` uses the schema version `read-paper.v1` and includes:

- `paper_ref`: raw input, normalized arXiv ID, canonical arXiv URLs.
- `metadata`: title, authors, abstract, categories, publication dates, DOI, comments.
- `artifacts`: important output paths.
- `ocr`: OCR paths plus page and region counts.
- `sections`: heading outline extracted from OCR markdown.
- `figures`: copied figure assets and any missing OCR image references.

Use this file before reading the full OCR markdown so you can discover the important paths quickly.
Use `sections` as internal context for understanding the paper structure, not as a standalone `OCR Outline` section in `report.md` unless the user explicitly asks for it.

## Report-writing checklist

When filling in `report.md`:

1. Preserve the frontmatter.
2. Replace every placeholder sentence in `Reading Report`.
3. Summarize the paper's problem, method, experiments, strengths, and limitations.
4. Embed only the most relevant figures in the analysis body with `![[assets/...]]`.
5. Keep the complete figure inventory in `Figure Appendix`.
6. Call out OCR uncertainty explicitly instead of silently normalizing it away.
7. Do not keep or add an `OCR Outline` section in the final report unless the user explicitly requests it.
8. Do not leave links or metadata fields pointing to transient OCR files that will be pruned from the final Obsidian folder.
9. After the report is complete, delete transient artifacts so only `report.md` and referenced assets remain.

## Frontmatter expectations

The scaffold already writes these properties:

- `title`
- `aliases`
- `authors`
- `published`
- `updated`
- `year`
- `source`
- `note_type`
- `arxiv_id`
- `arxiv_url`
- `pdf_url`
- `primary_category`
- `categories`
- `doi`
- `journal_ref`
- `comment`
- `tags`
- `status`
- `reading_stage`
- `created`
- `updated_note`
- `citation_key`
- `assets_dir`

If you edit the frontmatter, keep the existing keys unless a value is genuinely unavailable.

## Failure handling

- Metadata fetch failure: stop and show the exact arXiv API error.
- PDF download failure: stop and show the exact download error.
- OCR failure: stop and show the exact `use-ocr` error.
- Missing figure assets: continue, but keep the missing filenames in `Figure Appendix` or the final summary.
