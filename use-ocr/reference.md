# use-ocr reference

## Local stack

This skill standardizes on the following local stack:

- `uv` manages the `glmocr[selfhosted]` installation.
- `ollama` serves the local model runtime.
- `glm-ocr:latest` is the required model.
- `~/.local/glm-ocr/config.yaml` is the shared config file.

## Setup contract

Run this command when `glmocr` is missing, `ollama` is missing, the model is absent, or the config file does not exist:

```bash
bash {baseDir}/scripts/setup-local-ocr.sh
```

The script is idempotent and will:

1. Ensure macOS is being used.
2. Install `uv` if it is missing.
3. Install or upgrade `glmocr[selfhosted]` with `uv`.
4. Install `ollama` if it is missing.
5. Start `ollama serve` in the background if the service is not reachable.
6. Pull `glm-ocr:latest` if the model is missing.
7. Write `~/.local/glm-ocr/config.yaml`.

## Shared config

The setup script does not write a minimal YAML file from scratch. It starts from the default `glmocr` config bundled with the installed SDK, then overrides the fields required for the local Ollama flow:

```yaml
pipeline:
  maas:
    enabled: false

  ocr_api:
    api_host: localhost
    api_port: 11434
    api_path: /api/generate
    model: glm-ocr:latest
    api_mode: ollama_generate
```

This keeps the SDK's default layout configuration, including the bundled `PP-DocLayoutV3_safetensors` layout model setting, while redirecting OCR requests to Ollama's native `/api/generate` endpoint.

## Preferred commands

### Recommended entrypoint

For most saved-output workflows, use the wrapper instead of calling `glmocr parse` directly.

### Default temporary output under `/tmp`

```bash
python {baseDir}/scripts/run_use_ocr.py paper.pdf
```

This creates a temporary output root such as `/tmp/use-ocr-paper-a1b2c3d4/`.

### Explicit output directory override

```bash
python {baseDir}/scripts/run_use_ocr.py paper.pdf --output ./xxx/x
```

Use this when the caller wants artifacts in a stable project path instead of `/tmp`.

If the user's request looks like `/use-ocr --output ./xxx/x`, treat that as an explicit output-root override and honor it.

### Single file to stdout

```bash
glmocr parse image.png --config ~/.local/glm-ocr/config.yaml --mode selfhosted --stdout --no-save
```

### Single file and save outputs

```bash
glmocr parse image.png --config ~/.local/glm-ocr/config.yaml --mode selfhosted --output ./output
```

### Batch directory

```bash
glmocr parse ./docs --config ~/.local/glm-ocr/config.yaml --mode selfhosted --output ./output --no-layout-vis
```

### JSON only

```bash
glmocr parse image.png --config ~/.local/glm-ocr/config.yaml --mode selfhosted --stdout --no-save --json-only
```

The current CLI prepends a banner such as `=== file-name - JSON Result ===` before the JSON payload. If a downstream step needs strict JSON, either strip that banner first or save outputs to disk and read the generated `.json` file.

### Pipeline-safe output

For downstream automation, prefer the wrapper:

```bash
python {baseDir}/scripts/run_use_ocr.py image.png
```

This runs OCR, writes a temporary output root under `/tmp` by default, and creates:

- `run-summary.json` at the output root
- `parsed/<document>/pipeline-output.json` for each parsed document

Use `--output <dir>` to override the default `/tmp` location.

### Advanced manual flow

Use the raw two-step flow only when you need tighter control over intermediate files:

```bash
glmocr parse image.png --config ~/.local/glm-ocr/config.yaml --mode selfhosted --output ./output --no-layout-vis
python {baseDir}/scripts/build_pipeline_output.py --parsed-dir ./output/image --source image.png
```

This generates `./output/image/pipeline-output.json`, a normalized contract for later pipeline stages.

## Pipeline output contract

`pipeline-output.json` uses the versioned schema `use-ocr.v1`.

`run-summary.json` and the wrapper's stdout use the versioned schema `use-ocr.run.v1`.

Top-level fields:

- `schema_version`: Contract version for compatibility checks.
- `ok`: Success flag for the normalization step.
- `document_id`: Derived from the parsed document directory.
- `source`: Original input path and coarse type.
- `artifacts`: Absolute paths to raw and normalized outputs.
- `stats`: Page count, region count, and label counts.
- `content`: Full markdown plus grouped flattened regions.
- `pages`: Page-preserving region view.

Use this normalized file for orchestration, routing, and downstream extraction. If a later step needs untouched OCR metadata such as polygons or model-specific fields, read `artifacts.regions_path` or `artifacts.model_path`.

## Request shaping

Use the same local command path for all OCR variants, but tailor the response:

- General OCR: return the extracted Markdown text.
- Tables: keep Markdown table structure intact and call out the table section.
- Formulas: ask once whether to preserve LaTeX or convert it to a readable plain-text rendering.
- Handwriting: mention uncertainty when the output is ambiguous.

## Failure handling

- `glmocr: command not found`
  Run the setup script again and ensure `~/.local/bin` is on `PATH`.

- `ollama: command not found`
  Run the setup script again. On macOS it will use Homebrew when available and fall back to the official installer.

- `connection refused` or `could not connect to ollama`
  Start or restart the local service with `ollama serve`.

- `model not found`
  Run `ollama pull glm-ocr:latest`.

- Missing config file
  Re-run the setup script to regenerate `~/.local/glm-ocr/config.yaml`.

## References

- GLM-OCR Ollama deployment guide: <https://raw.githubusercontent.com/LuYanFCP/GLM-OCR/main/examples/ollama-deploy/README.md>
- GLM-OCR official skills index: <https://github.com/zai-org/GLM-OCR/tree/main/skills>
- Normalized output schema: [output-schema.md](output-schema.md)
