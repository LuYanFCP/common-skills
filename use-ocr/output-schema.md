# Pipeline Output Schema

Use `scripts/build_pipeline_output.py` to turn saved `glmocr` artifacts into a stable JSON contract for downstream automation.

For most workflows, prefer `scripts/run_use_ocr.py`. It defaults to a temporary output root under `/tmp` and prints a run-summary JSON to stdout.

## Recommended command

```bash
python {baseDir}/scripts/run_use_ocr.py paper.pdf
```

To store outputs in a fixed location instead of `/tmp`:

```bash
python {baseDir}/scripts/run_use_ocr.py paper.pdf --output ./xxx/x
```

The wrapper writes:

- `<output-root>/run-summary.json`
- `<output-root>/parsed/<document>/pipeline-output.json`

## Run Summary Schema

`run-summary.json` and the wrapper's stdout use `use-ocr.run.v1`:

```json
{
  "schema_version": "use-ocr.run.v1",
  "ok": true,
  "input_path": "/absolute/path/to/paper.pdf",
  "output_root": "/tmp/use-ocr-paper-a1b2c3d4",
  "parsed_root": "/tmp/use-ocr-paper-a1b2c3d4/parsed",
  "run_summary_path": "/tmp/use-ocr-paper-a1b2c3d4/run-summary.json",
  "used_temporary_output": true,
  "documents": [
    {
      "document_id": "paper",
      "source_path": "/absolute/path/to/paper.pdf",
      "parsed_dir": "/tmp/use-ocr-paper-a1b2c3d4/parsed/paper",
      "pipeline_output_path": "/tmp/use-ocr-paper-a1b2c3d4/parsed/paper/pipeline-output.json"
    }
  ]
}
```

## Command

```bash
python {baseDir}/scripts/build_pipeline_output.py \
  --parsed-dir <output>/<document-stem> \
  --source <input-file>
```

By default, this writes `pipeline-output.json` inside the parsed document directory.

## Schema

```json
{
  "schema_version": "use-ocr.v1",
  "ok": true,
  "document_id": "paper",
  "source": {
    "path": "/absolute/path/to/paper.pdf",
    "type": "pdf"
  },
  "artifacts": {
    "parsed_dir": "/absolute/path/to/parsed/paper",
    "markdown_path": "/absolute/path/to/parsed/paper/paper.md",
    "regions_path": "/absolute/path/to/parsed/paper/paper.json",
    "model_path": "/absolute/path/to/parsed/paper/paper_model.json",
    "pipeline_output_path": "/absolute/path/to/parsed/paper/pipeline-output.json"
  },
  "stats": {
    "page_count": 12,
    "region_count": 162,
    "label_counts": {
      "image": 15,
      "table": 5,
      "text": 142
    }
  },
  "content": {
    "markdown": "# Full OCR markdown...",
    "text_regions": [],
    "table_regions": [],
    "formula_regions": [],
    "image_regions": []
  },
  "pages": [
    {
      "page_index": 0,
      "region_count": 10,
      "regions": [
        {
          "page_index": 0,
          "region_index": 0,
          "label": "text",
          "native_label": "text",
          "bbox_2d": [94, 106, 901, 142],
          "content": "..."
        }
      ]
    }
  ]
}
```

## Field Notes

- `schema_version`: Versioned contract for downstream compatibility checks.
- `ok`: Always `true` when the normalized file is written successfully.
- `document_id`: Derived from the parsed document directory name.
- `source`: Original input file when known. `type` is `pdf`, `image`, or `unknown`.
- `artifacts`: Absolute paths to the raw `glmocr` outputs and the normalized pipeline file.
- `stats`: Fast summary fields for routers, monitors, or schedulers.
- `content.markdown`: Full OCR markdown text for document-level processing.
- `content.*_regions`: Flattened region lists grouped by pipeline-friendly category.
- `pages`: Page-preserving view of the OCR output for workflows that need positional context.

## Region Object

Every region inside `content.*_regions` and `pages[].regions` uses the same shape:

```json
{
  "page_index": 0,
  "region_index": 0,
  "label": "text",
  "native_label": "text",
  "bbox_2d": [94, 106, 901, 142],
  "content": "..."
}
```

## What Is Not Included

- Polygon masks are not copied into `pipeline-output.json` to keep the normalized file smaller.
- If downstream logic needs raw polygons, model-specific metadata, or any untouched OCR fields, read `artifacts.regions_path` or `artifacts.model_path`.
