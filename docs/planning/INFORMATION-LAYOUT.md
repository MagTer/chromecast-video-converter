# Information layout: aligning chromecast-video-converter with the shared rule

**Status 2026-10-29: PLAN, unsigned. Nothing below is built.** Origin: operator, 2026-10-29
— *"README i rooten är främst för människor, agents.md är projektspecifika tillägg."* The
rule is agent-config `proposals/2026-10-29-project-information-layout.md` (U1 there,
unsigned). **Runs only after that U1 is signed.**

## 1. What is here, read 2026-10-29 at `e923386`

| Reading | Value |
|---|---|
| `CLAUDE.md` | 1 089 words, already vendor-neutral ("Agent Guide") — within budget |
| `GEMINI.md` | 34 words: a pointer to `CLAUDE.md` |
| `docs/02_ai_agent_process.md` | 518 words of agent process ("Documentation first", "Plan before execution", GPU-conscious, observability) — four of its five principles restate `shared/core.md`; the GPU one is project-specific |
| `AGENTS.md`, skills, position file | none |

## 2. Where each part goes

| Today | Goes to |
|---|---|
| `CLAUDE.md` content | **`AGENTS.md`** (`git mv`); `CLAUDE.md` = `@AGENTS.md` |
| `GEMINI.md` | removed: agy's global file is `~/.gemini/config/AGENTS.md` (agent-config `shared/product.list`); whether agy reads a project `AGENTS.md` is **not measured**, so U1 measures it first and keeps `GEMINI.md` as a one-line pointer to `AGENTS.md` if it does not |
| `docs/02_ai_agent_process.md` | its project-specific rule (GPU-only transcoding, NVENC checks) into AGENTS.md; the rest is `core.md` and leaves the tree, sha named in the commit |

## 3. Units

Gates: `python scripts/code_check.py` (ruff, black, mypy, pytest per service).

### U1
- **Files.** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `docs/02_ai_agent_process.md`, references to
  any of them (`git grep -n 'CLAUDE.md\|GEMINI.md\|02_ai_agent'`), `.gitignore` (`.agent-state.md`).
- **Acceptance.** the grep finds no stale reference; `git check-ignore .agent-state.md`; the
  GPU rule is in AGENTS.md; gates green.
