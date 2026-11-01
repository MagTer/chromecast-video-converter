# Information layout: aligning chromecast-video-converter with the shared rule

**Status: PLAN, SIGNED by the operator 2026-11-01. Nothing below is built yet.** Origin: operator, 2026-10-29
— *"README i rooten är främst för människor, agents.md är projektspecifika tillägg."* The
rule is agent-config `docs/planning/2026-10-29-project-information-layout.md` (U1 there,
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
| `GEMINI.md` | removed: agy reads a workspace `AGENTS.md` and a `GEMINI.md` alike — MEASURED 2026-09-07 with canary tokens (agent-config `clients/agy.md`, "The project's own instruction file …") — so `AGENTS.md` alone reaches it. It attaches the file lazily, when a file in this directory is opened, not at turn 0; that section of its profile tells it to open the project file itself. (Corrected 2026-10-31: this row said whether agy reads a project `AGENTS.md` was not measured, and had U1 measure it.) |
| `docs/02_ai_agent_process.md` | its project-specific rule (GPU-only transcoding, NVENC checks) into AGENTS.md; the rest is `core.md` and leaves the tree, sha named in the commit |

## 3. Units

Gates: `python scripts/code_check.py` (ruff, black, mypy, pytest per service).

### U1
- **Files.** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `docs/02_ai_agent_process.md`, references to
  any of them (`git grep -n 'CLAUDE.md\|GEMINI.md\|02_ai_agent'`), `.gitignore` (`.agent-state.md`).
- **Acceptance.** the grep finds no stale reference; `git check-ignore .agent-state.md`; the
  GPU rule is in AGENTS.md; gates green.
