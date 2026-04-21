---
name: use-ocr
description: Run local OCR on macOS using `glmocr[selfhosted]` with Ollama `glm-ocr:latest`. Use when the user wants to extract text, tables, formulas, or handwriting from images or PDFs locally, mentions OCR/文字识别/文档解析, or wants GLM-OCR without a cloud API key.
---

# use-ocr

Use local GLM-OCR through Ollama on macOS. Prefer this skill over cloud OCR flows unless the user explicitly asks for a remote API.

## When to use

- Extract text from screenshots, scans, photos of documents, or PDFs.
- Parse tables, formulas, or handwriting from images and PDFs.
- Run OCR locally with `ollama` instead of a cloud key.
- User mentions `glmocr`, `GLM-OCR`, `ollama`, `OCR`, `文字识别`, `文档解析`, `表格识别`, `公式识别`, or `手写识别`.

## First use

1. Run `bash {baseDir}/scripts/setup-local-ocr.sh`.
2. Confirm `glmocr`, `ollama`, and `~/.local/glm-ocr/config.yaml` are ready.
3. If setup fails, stop and show the exact error.

## Standard workflow

1. Confirm the user's target:
   - General OCR
   - Table extraction
   - Formula extraction
   - Handwriting recognition
2. Reuse `~/.local/glm-ocr/config.yaml`. Do not create ad hoc local configs unless the user asks.
3. For temporary saved outputs, prefer this wrapper:

   ```bash
   python {baseDir}/scripts/run_use_ocr.py <input>
   ```

   It defaults to a temporary output root under `/tmp/use-ocr-<document>-<token>` and writes:
   - `run-summary.json` at the output root
   - `pipeline-output.json` inside each parsed document directory

4. If the user explicitly requests `/use-ocr --output ./xxx/x` or otherwise provides `--output <dir>`, pass that directory to the wrapper and do not use `/tmp`.

5. Prefer this raw command for interactive stdout-only use:

   ```bash
   glmocr parse <input> --config ~/.local/glm-ocr/config.yaml --mode selfhosted --stdout --no-save
   ```

6. Add `--json-only` when the user only wants structured output.
7. When the result must be parsed programmatically, prefer reading the saved `.json` file from the parsed output directory. `--stdout --json-only` may include a banner line before the JSON payload.
8. Add `--no-layout-vis` when faster output matters more than layout images.
9. When a downstream pipeline needs a stable machine-readable contract, prefer `scripts/run_use_ocr.py` because it runs OCR and normalization in one step.

## Output rules

- Show the OCR result clearly and do not invent missing text.
- If the command reports an error, show the exact error and stop.
- For table-focused requests, preserve Markdown table blocks.
- For formula-focused requests, ask once whether the user wants raw LaTeX or a more readable plain-text rendering, then remember that preference for the session.
- For handwriting-focused requests, keep uncertain fragments explicit instead of silently normalizing them.
- If results are written to disk, mention the output path.
- For pipeline-driven tasks, prefer returning the normalized `pipeline-output.json` path together with the raw artifact paths.

## Constraints

- This skill is for macOS local setup and execution only.
- Prefer the self-hosted Ollama path. Do not switch to MaaS unless the user explicitly asks.
- Reuse the installed model `glm-ocr:latest`.
- Keep installation idempotent by re-running the setup script when binaries, model, or config are missing.

## Additional resources

- Detailed commands and troubleshooting: [reference.md](reference.md)
- Pipeline JSON contract: [output-schema.md](output-schema.md)
- Environment bootstrap script: `scripts/setup-local-ocr.sh`
- Unified OCR runner: `scripts/run_use_ocr.py`
- Normalizer script: `scripts/build_pipeline_output.py`
