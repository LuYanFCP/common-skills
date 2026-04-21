# common-skills

A personal collection of reusable **Agent Skills**. Each top-level directory in this repo is a self-contained skill that an AI coding agent (Cursor, Claude Code, etc.) can load and follow.

The repository is managed in a **skills-first** layout: there is no application code at the root — only a tree of skills, each described by its own `SKILL.md`.

## Repository layout

```text
common-skills/
├── LICENSE
├── README.md                  # this file
├── SKILL.md                   # repo-level placeholder skill
├── use-ocr/                   # skill: local GLM-OCR via Ollama
│   ├── SKILL.md
│   ├── reference.md
│   ├── output-schema.md
│   └── scripts/
└── read-paper/                # skill: arXiv paper → Obsidian report
    ├── SKILL.md
    ├── reference.md
    └── scripts/
```

Each skill folder follows the same convention:

- `SKILL.md` — the entry point. YAML frontmatter (`name`, `description`) + when-to-use rules + the standard workflow the agent must follow.
- `reference.md` — long-form reference: full commands, flags, output contracts, and troubleshooting. Linked from `SKILL.md` so the agent can pull it in only when needed.
- `scripts/` — executable helpers (Python 3.12 / Bash) that implement the workflow. Skills should call these wrappers instead of re-deriving commands every run.
- `output/` *(gitignored)* — local working artifacts produced by the scripts.

## Available skills

| Skill | Purpose | Entry point |
| --- | --- | --- |
| [`use-ocr`](use-ocr/SKILL.md) | Run local OCR on macOS using `glmocr[selfhosted]` + Ollama `glm-ocr:latest`. Extract text, tables, formulas, and handwriting from images and PDFs without a cloud API key. | `python use-ocr/scripts/run_use_ocr.py <input>` |
| [`read-paper`](read-paper/SKILL.md) | Download an arXiv paper, parse it with `use-ocr`, and produce an Obsidian-ready Markdown report with frontmatter and copied figure assets. | `python3.12 read-paper/scripts/run_read_paper.py <arxiv-id-or-url>` |

`read-paper` depends on `use-ocr`, which is a good example of how skills in this repo are intended to compose: a higher-level skill orchestrates a lower-level one through its scripts and JSON output contract (`pipeline-output.json`).

## Install

```bash
npx skills add LuYanFCP/common-skills
```

Powered by the [`vercel-labs/skills`](https://github.com/vercel-labs/skills) CLI — works with Cursor, Claude Code, Codex, OpenCode, and ~40 other agents.

## Using a skill from an agent

Most agents discover skills by reading `SKILL.md` files. The agent reads each skill's `SKILL.md` (frontmatter + workflow), pulls in `reference.md` only when it needs long-form details, and invokes the helpers under `scripts/`.

In `SKILL.md` files, paths are written as `{baseDir}/scripts/...` where `{baseDir}` is the absolute path of the skill folder on disk.

## Adding a new skill

1. Create a new top-level directory named after the skill, e.g. `my-skill/`.
2. Add a `SKILL.md` with YAML frontmatter:

   ```markdown
   ---
   name: my-skill
   description: One sentence on what this skill does and when to use it.
   ---

   # my-skill

   ## When to use
   - …

   ## Standard workflow
   1. …
   ```

3. Add `reference.md` for any commands, flags, schemas, or troubleshooting that would otherwise bloat `SKILL.md`.
4. Put executable helpers under `scripts/`. Prefer:
   - Python 3.12 with full type annotations.
   - English-only comments.
   - Idempotent setup scripts.
   - A stable JSON output contract (e.g. `pipeline-output.json`) when the skill is meant to be composed by other skills.
5. If the skill writes local artifacts, add the corresponding `output/` directory to `.gitignore`.

## Conventions

- **Python**: 3.12, type-annotated everywhere, `typing` features from 3.12.
- **Comments**: English only.
- **Output discipline**: skills should not invent results; on failure they stop and surface the exact error.
- **Composability**: prefer scripts that emit a versioned JSON contract over scripts that only print to stdout.

## License

[MIT](LICENSE) © 0xNullPath
