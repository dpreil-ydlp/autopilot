# Autopilot - Dual-Agent Coding Loop

**Status**: 🚧 Under Active Development (Phase 1 - Foundation)

Autopilot is a local-first controller that orchestrates a "builder + reviewer" loop using Claude Code CLI by default (builder) and Codex CLI/OpenAI API (planner/reviewer). The system executes task files as a state machine with multi-worker DAG scheduling, validation, UAT, and git operations. You can optionally use Codex CLI as the builder if you prefer a more deterministic, diff-only workflow.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Orchestrator State Machine               │
│  INIT → PRECHECK → PLAN → SCHEDULE → DISPATCH → MONITOR     │
│    → FINAL_UAT → COMMIT → PUSH → DONE                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ├─→ Worker Loop (per task)
                              │   BUILD → VALIDATE → REVIEW
                              │   → UAT_GENERATE → UAT_RUN
                              │   → DECIDE → [FIX loop] → DONE
                              │
                              ├─→ Scheduler (multi-worker DAG)
                              └─→ Safety Guards & Recovery
```

## Installation

### Prerequisites

- Python 3.11+
- Git
- Claude Code CLI (installed and in PATH)
- Optional: Codex CLI (required if you set `planner.mode: codex_cli` or `reviewer.mode: codex_cli`, or if `builder.mode: codex_cli`)
- Optional: OpenAI API key (only required if using `openai_api` modes)

### Setup

```bash
# Clone repository
git clone <repo-url>
cd autopilot

# Install (recommended)
./install.sh

# Or double-click the installer
open "Install Autopilot.command"

# Or install dependencies directly
pip install -e .

# You can also run this from any directory by passing the repo path:
# pip install -e /path/to/autopilot

# Initialize configuration
autopilot init

# Customize configuration
vim .autopilot/config.yml
```

Tip: If you already had Autopilot installed via pipx, re-running `./install.sh` will now upgrade it
(pipx install uses `--force`). You can confirm which binary you're running with:

```bash
autopilot doctor
autopilot --version
```

## Quick Start

### 1. Create a Task

Create `tasks/feature-auth.md`:

```markdown
# Task: Implement JWT Authentication

## Goal
Add JWT-based authentication to the API.

## Acceptance Criteria
- [ ] POST /auth/login returns JWT token
- [ ] POST /auth/register creates user account
- [ ] Protected endpoints validate JWT
- [ ] Token expiration set to 15 minutes

## Allowed Paths
- src/auth/
- src/middleware/
- tests/auth/

## Validation Commands
```yaml
tests: pytest -q
lint: ruff check .
```
```

Notes:
- If a task omits **Validation Commands**, Autopilot will use the defaults from `.autopilot/config.yml`.
- Pytest exit code 5 ("no tests collected") can be treated as success for both `tests` and `uat` via
  `loop.allow_no_tests`.
 - If a task omits **UAT** in Validation Commands, Autopilot will use the default `commands.uat` from `.autopilot/config.yml`.
 - Plan/review runs use an isolated Codex profile (no MCP servers) and may override the Codex model; see config below.

### 2. Run Autopilot

```bash
# Run single task
autopilot run tasks/feature-auth.md

# Run from plan file
autopilot run --plan plan.md

# Run tasks in queue (lexical order)
autopilot run --queue tasks/

# Resume interrupted run
autopilot run --resume
```

### 3. Monitor Progress

```bash
# Check status
autopilot status

# View live dashboard (verbose output)
autopilot --verbose run --plan plan.md

# Pause/resume
autopilot pause
autopilot unpause

# Stop at safe boundary
autopilot stop

# Recover disk space / clean stale artifacts
autopilot recover
```

Note: `autopilot recover` no longer terminates running Codex processes by default. If you want that,
run `autopilot recover --kill-codex` explicitly.

## Configuration

Configuration file: `.autopilot/config.yml`

When `planner.disable_mcp` / `reviewer.disable_mcp` are enabled, Autopilot runs Codex under an isolated HOME
(default: `.autopilot/codex-home`) so your global `~/.codex` MCP server settings do not apply. Autopilot also
passes `planner.model` / `reviewer.model` and `model_reasoning_effort` to Codex, so plan/review runs may use a
different model than your interactive Codex default.

```yaml
repo:
  root: /path/to/repo
  default_branch: main  # or master, match your repo
  remote: origin

commands:
  format: ruff format .
  lint: ruff check .
  tests: pytest -q
  uat: pytest -q tests/uat

orchestrator:
  planner_timeout_sec: 300
  max_planner_retries: 1

loop:
  max_iterations: 10
  build_timeout_sec: 600
  validate_timeout_sec: 120
  review_timeout_sec: 180
  allow_no_tests: true

safety:
  allowed_paths: ["src/", "tests/"]
  denied_paths: []
  diff_lines_cap: 1000
  max_todo_count: 0

reviewer:
  mode: codex_cli  # or openai_api
  model: gpt-5.2-codex
  model_reasoning_effort: medium
  disable_mcp: true
  codex_home: null   # optional; overrides AUTOPILOT_CODEX_HOME

planner:
  mode: codex_cli  # or openai_api
  model: gpt-5.2-codex
  model_reasoning_effort: medium
  disable_mcp: true
  codex_home: null   # optional; overrides AUTOPILOT_CODEX_HOME

builder:
  mode: claude  # or codex_cli or openai_api
  cli_path: claude
  max_retries: 1
  permission_mode: bypassPermissions
  stream_output: true
  stream_log_interval_sec: 1.5
  system_prompt: null

  # Codex builder settings (used when mode: codex_cli/openai_api)
  model: gpt-5.2-codex
  model_reasoning_effort: medium
  disable_mcp: true
  codex_home: null   # optional; overrides AUTOPILOT_CODEX_HOME
```

Validation commands are executed through a shell (`bash -lc` on macOS/Linux), so `&&`, env vars, and other
common shell patterns work as expected.

## Task File Format

Tasks are Markdown files with structured sections:

- **Goal**: What should be accomplished
- **Acceptance Criteria**: Definition of done (checklist)
- **Constraints**: Technical limitations or requirements
- **Allowed Paths**: Restrict changes to specific directories
- **Validation Commands**: Commands to verify implementation (optional; defaults to config)
- **User Acceptance Tests**: Manual UAT scenarios
- **Notes**: Additional context

## Plan File Format

Plans define multiple tasks with dependencies:

```markdown
# Plan: Build Authentication System

## Overview
Implement complete authentication system with JWT tokens.

## Tasks
1. Setup project structure (no dependencies)
2. Implement core models (depends on 1)
3. Build API endpoints (depends on 2)
4. Write tests (depends on 2, 3)
5. Create documentation (depends on 3)
```

Autopilot expands plans into task DAGs and executes in parallel where possible.

Plan runs also persist debugging artifacts:
- Raw planner output: `.autopilot/plan/dag_raw.json`
- Normalized DAG used for scheduling: `.autopilot/plan/dag.json`

## Development Status

### ✅ Phase 1: Foundation (M1) - **COMPLETE**

- [x] Project structure & configuration
- [x] Core data models (config, state)
- [x] State machine implementation (orchestrator, worker)
- [x] Agent CLI wrappers (Claude, Codex)
- [x] Subprocess management with timeouts
- [x] Git operations wrapper
- [x] Logging & status dashboard
- [x] Template files & schemas

### 🚧 Phase 2: Robustness (M2) - **IN PROGRESS**

- [ ] Multi-worker DAG scheduler
- [ ] Task file parser & plan expander
- [ ] Validation & UAT execution
- [ ] Retry policies & error recovery
- [ ] Scope guards enforcement
- [ ] Kill switch implementation

### 📋 Phase 3: GitHub Integration (M3)

- [ ] Push & PR creation
- [ ] PR description templates
- [ ] Final gate CI checks

### 📋 Phase 4: Polish (M4)

- [ ] Enhanced console UX (live dashboard)
- [ ] Task queue tooling
- [ ] Metrics & reporting
- [ ] Documentation completion

## Safety Features

### Kill Switches

- `AUTOPILOT_STOP` - Halt at next safe boundary
- `AUTOPILOT_PAUSE` - Pause before next transition
- `AUTOPILOT_SKIP_REVIEW` - Emergency mode (logged)

### Scope Guards

- Allowed/denied path enforcement
- Diff line count caps
- TODO/FIXME detection
- Network tool prohibition (optional)
- Per-task UAT artifacts under `tests/uat/` are treated as in-scope even when task `allowed_paths` are narrower.
- Common doc artifact auto-fix: root-level `*.md` created out-of-scope is moved into the first allowed
  directory when possible, otherwise backed up under `.autopilot/artifacts/out-of-scope/`

### Merge Robustness

Autopilot merges task branches back into the default branch using `--no-ff`. If the merge is blocked by
unrelated local changes (e.g. dirty working tree in out-of-scope files), Autopilot falls back to a
scope-limited apply for that task's `allowed_paths` (and may use `--autostash` when appropriate).

## Commands Reference

```bash
# Initialization
autopilot init              # Create configuration
autopilot init --force      # Overwrite existing config

# Execution
autopilot run <task.md>     # Run single task
autopilot run --plan plan.md    # Run from plan
autopilot run --queue tasks/    # Run task queue
autopilot run --resume     # Resume interrupted run

# Control
autopilot status            # Show current status
autopilot stop              # Request safe halt
autopilot pause             # Pause execution
autopilot unpause           # Resume from pause

# Options
--quiet                     # Minimal output (run command)

# Global options
--verbose, -v               # Enable detailed output
--max-workers N             # Parallel workers
--pattern "fix-*"           # Filter tasks
```

## Architecture Details

### Orchestrator State Machine

Main controller managing overall execution flow:

```
INIT → PRECHECK → PLAN → SCHEDULE → DISPATCH → MONITOR
  → FINAL_UAT_GENERATE → FINAL_UAT_RUN → COMMIT → PUSH → DONE
```

Error states: `FAILED`, `PAUSED`

### Worker Loop State Machine

Per-task execution loop:

```
TASK_INIT → BUILD → VALIDATE → REVIEW
  → UAT_GENERATE → UAT_RUN → DECIDE
    → [FIX loop if needed] → TASK_DONE/TASK_FAILED
```

### Multi-Worker DAG Scheduler

- Maintains task states (PENDING/RUNNING/DONE/FAILED/BLOCKED)
- Computes ready set (tasks with all dependencies DONE)
- Dispatches tasks to worker pool up to `max_workers`
- Handles worker completion/failure
- Updates DAG state and unlocks downstream tasks

## Error Recovery

### Retry Policies

- **Planner failure**: retry 1x → FAILED
- **Builder failure**: retry 1x → FAILED
- **Reviewer failure**: retry 1x → FAILED
- **Reviewer requests changes**: feed review feedback into the next BUILD iteration
- **Push failure**: retry 2x → FAILED with patch artifact
- **Validation failure**: feed into FIX loop (no retry)

### Push Recovery

Classify stderr and handle appropriately:
- **Auth**: Run `gh auth status`, halt if unrecoverable
- **Non-fast-forward**: Fetch + rebase + retry
- **Network**: Backoff retry
- **Policy**: Halt + produce patch artifact

## Testing

```bash
# Run all tests
pytest

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# With coverage
pytest --cov=src --cov-report=html
```

## Project Structure

```
autopilot/
├── src/
│   ├── main.py              # CLI entrypoint
│   ├── config/
│   │   ├── models.py        # Pydantic config models
│   │   └── loader.py        # Config loader with validation
│   ├── state/
│   │   ├── machine.py       # Orchestrator state machine
│   │   ├── worker.py        # Worker task loop state machine
│   │   └── persistence.py   # State file I/O with atomic writes
│   ├── agents/
│   │   ├── base.py          # Base agent interface
│   │   ├── claude.py        # Claude Code CLI wrapper
│   │   └── codex.py         # Codex CLI/OpenAI wrapper
│   └── utils/
│       ├── git.py           # Git operations wrapper
│       ├── subprocess.py    # Subprocess management with timeouts
│       └── logging.py       # Structured logging
├── templates/
│   ├── task.md              # Task file template
│   ├── plan.md              # Plan file template
│   └── config.yml           # Config file template
├── schemas/
│   ├── review.json          # Reviewer JSON schema
│   └── plan.json            # Planner JSON schema
├── tests/
│   ├── unit/                # Unit tests
│   ├── integration/         # Integration tests
│   └── fixtures/            # Test fixtures
├── pyproject.toml           # Project dependencies
└── README.md
```

## Contributing

This is an active development project. See `IMPLEMENTATION_STATUS.md` and the phase summaries in the repo for roadmap details.

## License

MIT

## Next Steps

1. ✅ **Phase 1 Complete**: Core foundation implemented
2. 🚧 **Phase 2 In Progress**: Building robustness features
3. 📋 **Phase 3-4**: GitHub integration and polish

See `IMPLEMENTATION_PLAN.md` for complete details.
