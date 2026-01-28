# Autopilot Dual-Agent Coding Loop (Local-First) — PRD + Technical Spec

Version: 1.0  
Date: 2026-01-28  
Owner: Dovid Preil  
Status: Build-ready specification

## 1. Product overview

This project is a local-first controller that orchestrates a repeatable “builder + reviewer” loop:

- Builder agent: Claude Code CLI (“Claude Code”).
- Reviewer agent: ChatGPT-style review via Codex CLI (or OpenAI API), producing a strict JSON verdict.
- Controller: a local program that runs a state machine, passes artifacts between agents, runs validation commands, manages git commits/pushes, and maintains a continuously updated status log.

Guiding principle:
- Planning + review are owned by Codex; implementation + execution are owned by Claude Code.

Primary goals:
- Autopilot iteration without manual handoffs each cycle.
- Robustness: detect and recover from common jams (agent CLI errors, tests failing, git push failing, stuck processes).
- Repeatability: tasks are file-backed, each iteration is logged, results are reproducible.
- Visibility: single STATUS.md and append-only release notes, plus detailed logs per step.
- Safety: hard stop/kill switch, iteration caps, diff-size caps, and scope guards.

Non-goal:
- Fully replacing human oversight for high-risk changes. The system must support a “final gate” via GitHub PR/CI but should not require it for every micro-iteration.

## 2. Users and use cases

Primary user:
- A developer/ops user running a local repo who wants a hands-off build-review-fix loop.

Core use cases:
1) Run a single task file and converge to APPROVE + passing tests + passing UAT, then push a branch.
2) Run a queue of tasks sequentially, producing running release notes.
3) Pause or stop safely when behavior looks wrong.
4) Recover from common failures: CLI crashes, timeouts, network/auth errors on push, test failures.

## 3. Success criteria

A task is “DONE” when:
- Local validation commands succeed (tests, lint/format as configured).
- Reviewer verdict is APPROVE.
- User acceptance testing succeeds (or is explicitly skipped per config and recorded).
- Changes are committed to a dedicated branch.
- Optional: branch is pushed to remote and (if enabled) a PR is opened/updated.

The system is “successful” when:
- It can converge on typical tasks with minimal human intervention.
- It halts safely and produces actionable diagnostics when it cannot converge.

## 4. Operating model

Local loop is the primary iteration engine:
- Fast iterations, direct access to local environment, richer observability.

GitHub loop is an optional final gate:
- Push branch and open PR only when local loop reports DONE.
- CI can be required only for merge, not for iteration.

## 5. Requirements

### 5.1 Functional requirements

FR-1 Task-driven workflow
- The unit of work is a task file in `tasks/`.
- Each task includes: goal, acceptance criteria, constraints, validation commands, optional allowed paths.
- Controller reads one task file or a directory/queue.
- Alternate entrypoint: a single `plan.md` file can be provided; the controller will ask Codex to expand it into a dependency-ordered set of task files (a DAG) and then execute that task graph.

FR-2 Deterministic state machine
- Controller runs a strict state machine with persisted state.
- On restart, controller resumes from last checkpoint safely.

FR-3 Planning execution (Codex — plan + task graph)
- All planning is owned by Codex.
- Given a user-provided `plan.md` (or a single task), the controller invokes Codex to produce:
  - a concrete plan (ordered phases)
  - an executable task graph (DAG) of smaller task files with explicit dependencies (“blocks/blocked-by”)
  - a topological ordering and/or parallel batches that make the dependency structure explicit
  - a list of tasks that can run immediately in parallel (ready set)
  - per-task guidance for Claude Code: suggested skills, recommended MCP servers, and suggested sub-agent breakdown
  - a test plan covering functional, robustness, recovery, scheduling, and UX requirements (written to `tests/TEST_PLAN.md`)
- The planning output is saved as artifacts and materialized into per-task Markdown files that the orchestrator can track and update.
- The plan MUST be scoped to constraints and deny/allow paths (no unrelated refactors).

FR-4 Implementation execution (Claude Code)
- All implementation is owned by Claude Code.
- Controller invokes Claude Code CLI in non-interactive mode.
- Builder prompt includes only:
  - Task specification (one leaf task from the DAG)
  - Planning guidance for that task (skills/MCP/subagents recommendations)
  - Current repo status summary
  - Last reviewer JSON (if any)
  - Last validation failures (if any)
- Builder must produce a structured “run summary” at end of each iteration.
- Builder must follow the planned skills/sub-agent approach (or explain deviations) and is expected to:
  - explicitly state the skills it will use before starting edits
  - use `upscale` to pick up skills when beneficial/necessary
  - use MCP servers when relevant to reduce guesswork (e.g., framework docs, APIs, schemas)
  - delegate parallelizable subtasks to sub-agents when it improves throughput and quality

FR-5 Validation
- Claude Code runs configured commands (the controller executes/captures deterministically):
  - `format` (optional)
  - `lint` (optional)
  - `tests` (required)
- Captures exit codes and tail of output for prompts/logs.

FR-6 Reviewer execution (Codex)
- Reviewer consumes ONLY:
  - Git diff against base (or against last commit)
  - Validation output and exit codes
  - Optional: task acceptance criteria
- Reviewer output MUST match a JSON Schema:
  - verdict: APPROVE | REQUEST_CHANGES
  - summary: string
  - issues[] with severity (blocker/major/minor), message, fix instruction, optional file/line hints

FR-7 UAT generation (Codex)
- Controller generates/refreshes UAT cases for a task using Codex, based on the task’s Acceptance Criteria and the diff.
- Generated UAT cases are stored as artifacts (and optionally as executable test files if a UAT framework is configured).

FR-8 User Acceptance Testing (UAT)
- After reviewer output is produced, Claude Code runs UAT to verify the change works from a user perspective.
- UAT is driven by a configurable command (e.g., e2e tests, smoke tests, or scripted flows) and captured with exit codes and log tails.
- If UAT fails, the system iterates (implementation + review + UAT) until UAT passes or max iterations are exceeded.

FR-9 Iteration loop
- If tests fail OR reviewer verdict is REQUEST_CHANGES OR UAT fails, controller loops back to builder with the structured issues + failing logs.
- Iteration cap enforced per task.

FR-10 Git operations (controller-owned)
- Create a branch per task.
- Commit changes when moving into PUSH or DONE.
- Push branch (optional, configurable).
- If push fails, run recovery steps and/or produce patch artifacts.

FR-11 Observability outputs
- `STATUS.md` updated every state transition.
- `RELEASE_NOTES_RUNNING.md` append-only summary per completed task and per iteration.
- Logs stored under `.autopilot/logs/` with separate streams for controller, builder, reviewer, and validation.
- UAT artifacts stored under `.autopilot/uat/` (cases, logs, and per-run results).
- Plan/DAG artifacts stored under `.autopilot/plan/` (plan output, task graph, per-task MD status).
- Terminal output provides live, human-readable progress so the user can understand what the orchestrator is doing without opening files.

FR-12 Kill switch and pause
- If `AUTOPILOT_STOP` file exists, controller halts after current step boundary.
- If `AUTOPILOT_PAUSE` file exists, controller pauses before next state transition.
- Optional: `AUTOPILOT_SKIP_REVIEW` (emergency) is supported but must be clearly recorded and still requires passing tests.

FR-13 Scope and safety guards
- Optional allowed paths per task.
- Default deny list (configurable) for sensitive areas (e.g., billing, auth, infra).
- Diff-size cap (lines changed) to prevent runaway refactors.
- “No new TODO/FIXME” check (configurable).

FR-14 Multi-task scheduling (DAG execution)
- When the input is `plan.md` (or when planning chooses to break down work), the orchestrator executes a task DAG:
  - Maintains task state (PENDING/RUNNING/DONE/FAILED/BLOCKED) and dependency edges.
  - Continuously computes the “ready set” (tasks whose dependencies are DONE).
  - Dispatches tasks in the ready set to a worker pool (multiple Claude Code instances), up to a configured concurrency limit.
  - Records progress by updating the per-task Markdown files and a machine-readable task graph artifact.

FR-15 Paired worker loop (Claude + Codex per task)
- Each dispatched task is executed by a paired loop:
  - Claude Code implements and runs validations/UAT.
  - Codex reviews the diff for that task and returns strict JSON.
- The orchestrator uses the task’s dependencies to unlock downstream work as tasks complete.

FR-16 Terminal visual feedback (operator UX)
- The orchestrator prints continuous progress updates to the terminal while running:
  - current orchestrator state (PLAN/SCHEDULE/DISPATCH/MONITOR/etc.)
  - DAG summary (DONE/RUNNING/BLOCKED/PENDING counts)
  - per-worker status lines (task id/title, current step BUILD/VALIDATE/REVIEW/UAT, elapsed time)
  - explicit notices when parallel work starts (dispatch events) and when tasks unlock downstream tasks
  - last failure summaries (exit code + short tail pointer) with the path to the relevant log/artifact files
- The orchestrator SHOULD surface “what is happening” at the moment (e.g., “Claude running UAT”, “Codex reviewing diff”, “waiting on dependency X”).
- The orchestrator MUST support a quiet mode (minimal output) and a verbose mode (stream step output).

### 5.2 Non-functional requirements

NFR-1 Reliability
- Steps have timeouts.
- Stuck detection: if no output for a configured interval, kill subprocess and retry per policy.
- Crash consistency: state and task status updates must be atomic (write temp + fsync + rename) so power loss does not corrupt the run.
- Single-run locking: the orchestrator must acquire a lock (e.g., `.autopilot/lock`) to prevent two concurrent runs against the same repo/workspace.

NFR-2 Repeatability
- Inputs and outputs are file-backed.
- State is persisted to disk.
- Each iteration is recorded with exact commands and exit codes.

NFR-3 Portability
- Works on macOS/Linux.
- Minimal dependencies beyond Python 3.11+ (or Node, choose one implementation language).
- No need for Docker, but optional support.

NFR-4 Security
- Secrets are read from environment variables only.
- Logs must redact obvious secrets patterns (configurable).
- Avoid sending entire repo contents to models: only diff, task, and selected outputs.

NFR-5 Performance
- Controller overhead should be negligible relative to tests/agent calls.
- Avoid repeated large context sends; use capped tails and concise summaries.

## 6. User experience

Primary interface:
- CLI command: `autopilot run tasks/<task>.md`
- Secondary: `autopilot run --queue tasks/`
- Status: view `.autopilot/STATUS.md` and `.autopilot/logs/*`

Recommended workflow:
1) Create task file from template.
2) Run local autopilot loop.
3) Watch STATUS.md if needed.
4) When DONE, push branch and optionally open PR.
5) Merge via normal process (optional GitHub checks).

Terminal feedback expectations:
- During runs, the console shows a live “dashboard” view of progress (DAG + workers) and emits clear transitions (PLAN -> DISPATCH -> MONITOR, etc.).
- When running multiple workers, the console shows which tasks are running in parallel and their current steps.
- When Claude Code uses sub-agents (intra-task) or when the orchestrator uses multiple workers (inter-task), the console states that explicitly.

## 7. Files and repository layout

Required:
- `AGENTS.md` — shared definition of done and repo-specific commands.
- `tasks/` — task specifications.
- `plan.md` (optional) — a single high-level plan document that Codex expands into a task DAG.
- `.autopilot/` — controller runtime state and logs.
  - `.autopilot/config.yml`
  - `.autopilot/state.json`
  - `.autopilot/STATUS.md`
  - `.autopilot/logs/`
  - `.autopilot/artifacts/` (patch bundles, diffs, review.json archives)
  - `.autopilot/uat/` (generated UAT cases, logs, results)
  - `.autopilot/plan/` (Codex plan + task graph + per-task MD files)

Suggested:
- `RELEASE_NOTES_RUNNING.md` — append-only log.

## 8. Task file specification (Markdown)

File: `tasks/<YYYY-MM-DD>_<slug>.md`

Required sections:
- Title
- Goal
- Acceptance Criteria (bullet list, testable)
- Constraints (optional)
- Allowed Paths (optional)
- Validation Commands (optional overrides)
- User Acceptance Tests (optional; can be auto-generated by Codex)
- Notes (optional)

Example template:

```md
# Task: <title>

Goal:
- ...

Acceptance Criteria:
- ...
- ...

Constraints:
- Keep changes minimal.
- Do not refactor unrelated code.

Allowed Paths:
- src/
- tests/

Validation Commands:
- tests: pytest -q
- lint: ruff check .
- format: ruff format .
- uat: pytest -q tests/uat

User Acceptance Tests:
- <scenario 1>
- <scenario 2>

Notes:
- ...
```

## 8.1 Plan file specification (Markdown)

File: `plan.md`

Purpose:
- A single high-level plan that Codex expands into a dependency-ordered set of executable task files (a DAG).

Recommended sections:
- Title
- Goal
- Acceptance Criteria (testable)
- Constraints (optional)
- Allowed Paths / Deny Paths (optional)
- Validation Commands (optional defaults)
- User Acceptance Tests (optional; Codex can generate)
- Notes (optional)

Task graph materialization:
- Codex planning output is written to `.autopilot/plan/` and expanded into per-task Markdown files under `.autopilot/plan/tasks/`.
- Each generated per-task file should include dependency metadata (e.g., `depends_on:`) so the orchestrator can compute what blocks what and track status over time.
  - Recommended per-task frontmatter keys:
    - `id`, `title`, `status`, `depends_on[]`, `blocked_by[]`, `attempts`, `assigned_worker` (optional)

## 9. Configuration spec

File: `.autopilot/config.yml`

Minimum keys:

- repo:
  - base_branch: main
  - remote_name: origin
- commands:
  - tests: "<required>"
  - lint: "<optional>"
  - format: "<optional>"
  - uat: "<optional but recommended>"
- orchestrator:
  - max_workers: 3
- loop:
  - max_iterations: 5
  - diff_line_cap: 800
  - step_timeouts_sec:
      plan: 120
      build: 900
      validate: 600
      review: 180
      uat: 600
      push: 120
  - stuck_no_output_sec: 120
  - retries:
      build: 1
      review: 1
      push: 2
- safety:
  - deny_paths: ["infra/", "billing/"]
  - forbid_todos: true
- reviewer:
  - mode: "codex_cli"  # codex_cli | openai_api
  - schema_path: ".autopilot/review_schema.json"
- planner:
  - mode: "codex_cli"  # codex_cli | openai_api
  - schema_path: ".autopilot/plan_schema.json"
- builder:
  - mode: "claude_code_cli"
  - allowed_tools: ["Read", "Edit", "Bash"]
  - skill_policy:
      detect_skills: true
      allow_upscale: true
      use_mcp: true
      max_subagents: 4
      require_skill_plan: true
- github:
  - enabled: false
  - open_pr: false
  - pr_title_prefix: "[autopilot]"
- logging:
  - redact_patterns:
      - "(?i)api[_-]?key\s*[:=]\s*\S+"
      - "(?i)bearer\s+\S+"

## 10. State machine spec

There are two layers:
1) Orchestrator state machine (plan-level, DAG scheduling)
2) Worker task loop (per-task, paired Claude+Codex)

### 10.1 Orchestrator states
1) INIT
2) PRECHECK
3) PLAN (Codex expands `plan.md` into a DAG)
4) SCHEDULE (materialize task files + compute ready set)
5) DISPATCH (assign ready tasks to worker pool)
6) MONITOR (collect worker results, update DAG, unlock downstream tasks)
7) FINAL_UAT_GENERATE (Codex; optional)
8) FINAL_UAT_RUN (Claude Code; required if `commands.uat` configured)
9) COMMIT
10) PUSH (optional)
11) DONE
12) FAILED
13) PAUSED

Orchestrator transition rules (high level):
- INIT -> PRECHECK always
- PRECHECK -> PLAN when input loaded and repo is acceptable
- PLAN -> SCHEDULE always
- SCHEDULE -> DISPATCH always
- DISPATCH -> MONITOR always
- MONITOR:
  - if any task FAILED and retries exhausted -> FAILED
  - if tasks remain and workers available -> DISPATCH
  - if all tasks DONE -> FINAL_UAT_GENERATE
- FINAL_UAT_GENERATE -> FINAL_UAT_RUN always
- FINAL_UAT_RUN:
  - if uat_pass -> COMMIT
  - else -> DISPATCH (create/unlock a “UAT fix” task and iterate) until max_iterations exceeded
- COMMIT -> PUSH if github.enabled else DONE
- PUSH -> DONE on success
- PUSH -> FAILED (or patch artifact) after retries exhausted

### 10.2 Worker task loop states (per task)
1) TASK_INIT
2) BUILD (Claude Code)
3) VALIDATE (Claude Code runs commands.tests/lint/format as configured)
4) REVIEW (Codex)
5) UAT_GENERATE (Codex)
6) UAT_RUN (Claude Code)
7) DECIDE
8) FIX (alias of BUILD with “fix only” prompting)
9) TASK_DONE
10) TASK_FAILED

Worker transition rules:
- TASK_INIT -> BUILD always
- BUILD -> VALIDATE always (unless kill switch triggered)
- VALIDATE -> REVIEW always (even if failing; include failures)
- REVIEW -> UAT_GENERATE always
- UAT_GENERATE -> UAT_RUN always
- UAT_RUN -> DECIDE always
- DECIDE:
  - if tests_pass AND verdict==APPROVE AND uat_pass -> TASK_DONE
  - else if iteration < max_iterations -> FIX
  - else -> TASK_FAILED

Persisted state fields (in `.autopilot/state.json`):
- run_id
- started_by (optional)
- plan_path (optional)
- task_id, task_path
- branch_name
- iteration
- current_state
- timestamps: started_at, last_transition_at
- last_plan: exit_code, duration, log_path, plan_json_path, task_graph_path, tasks_dir
- last_build: exit_code, duration, log_path, summary_json_path
- last_validate: exit_code, duration, log_path
- last_review: verdict, issues_count, review_json_path
- last_uat_generation: exit_code, duration, log_path, uat_cases_path
- last_uat_run: exit_code, duration, log_path
- scheduler: max_workers, workers_active, ready_tasks_count, blocked_tasks_count
- tasks: [{id, status, depends_on[], assigned_worker}]  # snapshot of DAG execution
- git: last_commit_sha, pushed (bool)

Resume semantics:
- On startup, if state.json exists and current_state not in {DONE, FAILED}, resume from the last safe boundary:
  - If mid-step, mark step as failed and retry according to policy.
 - For multi-worker DAG runs, any task left in RUNNING when the process restarts is treated as interrupted:
   - release the worker assignment
   - set the task back to PENDING (or FAILED if attempts exceeded)
   - re-compute the ready set and continue dispatching

Workspace safety on resume:
- On resume, orchestrator must detect whether the workspace is clean enough to continue:
  - If working tree is clean: continue.
  - If dirty: create a safety snapshot artifact (patch/bundle) and either:
    - auto-stash and continue (if configured), or
    - fail fast with instructions and artifact paths.
- If using per-worker git worktrees (recommended), resume should enumerate worktrees and recover per-task branches similarly.

## 11. Builder interface spec (Claude Code CLI)

Invocation:
- Non-interactive prompt mode.
- Output captured to `.autopilot/logs/claude_code_<ts>.log`

Prompt contract:
- Must include task spec and constraints.
- Must include strict instruction to output a JSON summary at end.

Builder output summary JSON schema (saved by controller):
- changed_files: []
- commands_ran: [{cmd, exit_code}]
- tests_ran: boolean
- tests_passed: boolean
- uat_ran: boolean
- uat_passed: boolean
- skills_used: [string]
- subagents_used: [{name, purpose}] (optional)
- mcp_servers_used: [string] (optional)
- notes: string
- risks: string (optional)

If builder output does not contain parseable JSON, controller retries once with “return JSON only”.

## 11.1 Planner interface spec (Codex — plan.md expansion + task DAG)

Invocation:
- Non-interactive prompt mode, prior to any task execution.
- Output captured to `.autopilot/logs/plan_<ts>.log`

Planner output contract:
- Must output a single JSON object (no prose) saved by controller:
  - plan_summary: string
  - tasks_dir: ".autopilot/plan/tasks"
  - tasks: [{
      id,
      title,
      goal,
      acceptance_criteria[],
      allowed_paths[] (optional),
      validation_commands (optional overrides),
      depends_on[],
      suggested_claude_skills[] (optional),
      suggested_mcp_servers[] (optional),
      suggested_subagents[] (optional)
    }]
  - edges: [{from, to, reason}]  # explicit DAG edges (from blocks to)
  - topo_order: [task_id] (optional)
  - parallel_batches: [[task_id]] (optional)  # batches of tasks that can run concurrently
  - initial_ready_tasks: [task_id]
  - scope_notes: string
  - risks: string (optional)

The controller materializes `tasks[]` into per-task Markdown files under `.autopilot/plan/tasks/`, including dependency metadata, so the orchestrator can track what blocks what and update status over time.

## 11.2 Skills, upscale, MCP servers, and sub-agents (Claude Code)

Skill identification:
- At PLAN time, Codex should recommend Claude Code skills per task (in `suggested_claude_skills`).
- At BUILD time, Claude Code must report the skills actually used (in `skills_used`).

Upscale:
- `upscale` is treated as a first-class Claude Code skill used to acquire/enable new skills and/or capabilities mid-task when the default toolset is insufficient.
- If `upscale` is used, the builder summary MUST include what was gained (e.g., skill name(s) enabled) and why.

MCP servers:
- When `builder.skill_policy.use_mcp` is enabled, the orchestrator should encourage Claude Code to use MCP servers for authoritative context (documentation, APIs, schemas, etc.) instead of guessing.
- The builder summary MUST include `mcp_servers_used` when MCP servers were consulted.

Sub-agents:
- Claude Code may spawn sub-agents to parallelize work (e.g., “write tests”, “update docs”, “implement feature slice”).
- The planner should propose sub-agents in the per-task `suggested_subagents` field, and the builder should report `subagents_used` with each sub-agent’s purpose and deliverable.
- Note: Claude Code sub-agents are intra-task parallelism; the orchestrator worker pool (`orchestrator.max_workers`) is inter-task parallelism across the task DAG.

## 12. Reviewer interface spec (Codex)

Inputs:
- diff file for the specific task (task_base...task_head), not the entire plan unless explicitly configured
- validate output tail (e.g., last 200 lines)
- acceptance criteria excerpt (optional)

Output:
- Must validate against `.autopilot/review_schema.json`.

Reviewer must enforce:
- If tests failing: verdict MUST be REQUEST_CHANGES with at least one blocker issue referencing failing tests.
- If security/correctness risk: REQUEST_CHANGES with blocker or major.

## 12.1 User Acceptance Testing (UAT) interface spec

UAT generation (UAT_GENERATE):
- Uses Codex to draft/refresh UAT cases based on:
  - Task Acceptance Criteria
  - Current diff
- Writes a human-readable UAT cases artifact to `.autopilot/uat/<task_id>_uat.md`.
- Optional: if the repo has a configured UAT framework, Codex may also generate/adjust executable UAT tests under repo-defined paths (guarded by Allowed Paths / deny list).

UAT run (UAT_RUN):
- Claude Code runs `commands.uat` if configured (and captures output).
- If `commands.uat` is not configured, controller records UAT as skipped (with a clear note in STATUS.md) and treats `uat_pass=true` for DECIDE.
- If UAT fails, Claude Code is responsible for iterating (with Codex review) until UAT passes or the iteration cap is exceeded.

## 13. Robustness and recovery strategies

### 13.1 Subprocess and CLI failures
- If planner (Codex) CLI exits non-zero:
  - Retry planner once.
  - If fails twice, FAILED with logs and last stderr snippet.

- If builder CLI exits non-zero:
  - Retry builder once.
  - If fails twice, FAILED with logs and last stderr snippet.

- If reviewer CLI exits non-zero:
  - Retry reviewer once.
  - If fails twice, FAILED.

### 13.2 Stuck detection
- If no output for `stuck_no_output_sec`, kill process.
- Mark as “stuck” and retry per retry policy.

### 13.3 Validation failures
- Always feed failing output tail into next FIX prompt.
- FIX prompt must instruct “no refactors, only fix failing tests/issues”.

### 13.4 UAT failures
- Always feed UAT failure output tail (and the generated UAT cases file) into the next FIX prompt.
- Prefer minimal changes that make UAT pass without weakening tests or acceptance criteria.

### 13.5 Git push failures
Classify stderr into:
- auth
- non-fast-forward / protection
- network transient
- file size / policy

Recovery:
- auth: run `gh auth status` (if configured) and halt with explicit instruction if not recoverable.
- non-fast-forward: fetch + rebase and retry.
- network: backoff retries.
- policy: halt and produce patch artifact.

Fallback artifacts:
- `.autopilot/artifacts/<task_id>_<ts>.patch` from `git diff --binary`
- `.autopilot/artifacts/<task_id>_<ts>.bundle` from `git bundle`

### 13.6 Multi-worker conflicts and recovery
- The orchestrator should assume tasks may contend on files and/or create merge conflicts.
- Recommended mitigation: run each task in an isolated git worktree + branch (one worktree per worker), then merge/rebase into the main task branch only when the task is approved.
- If a merge/rebase conflict occurs:
  - classify as a recoverable failure for that task
  - feed the conflict markers + `git status` + failing command output into Claude Code FIX
  - retry within iteration cap
- Planning should reduce conflicts by keeping Allowed Paths tight and by separating tasks that touch the same areas into dependent (serialized) tasks where possible.

### 13.7 Power loss / machine restart recovery
- A hard power-off is treated the same as a crash:
  - state.json + per-task MD status are the source of truth
  - any RUNNING tasks are re-queued on restart
  - all subprocesses are assumed dead and re-run as needed
- The orchestrator must be able to restart via `autopilot resume` and continue toward DONE without manual intervention when possible.

## 14. CLI specification

Binary/entrypoint name: `autopilot`

Commands:
- `autopilot init`  
  Creates `.autopilot/` structure, config template, schema templates, and task template.

- `autopilot run <path> [--push] [--pr]`  
  Runs either:
  - a single task file (task loop), or
  - a `plan.md` file (Codex planning -> task DAG -> scheduled execution).

- `autopilot run --queue <tasks_dir> [--pattern <glob>]`  
  Runs multiple tasks in lexical order.

- `autopilot status`  
  Prints current state from `.autopilot/state.json` and path to STATUS.md.

- `autopilot status --watch`  
  Continuously refreshes status (useful for long multi-worker DAG runs).

- `autopilot resume`  
  Resumes from persisted state.

- `autopilot stop`  
  Creates `AUTOPILOT_STOP`.

- `autopilot pause` / `autopilot unpause`  
  Manage pause files.

Exit codes:
- 0: success (DONE)
- 2: paused
- 10: failed (non-recoverable)
- 11: failed (max iterations exceeded)
- 12: failed (push failure with artifacts produced)

Common flags:
- `--no-ui` disables the live dashboard output (still writes STATUS.md/logs).
- `--quiet` prints only high-level state transitions and final outcome.
- `--verbose` streams step output as it runs (in addition to logs on disk).

## 15. Security and privacy controls

- Never send full repository content to models.
- Default to diff-only context plus minimal tails of logs.
- Provide config option to redact patterns in logs and in prompts.
- Provide config option to disallow network tool usage by builder.

## 16. Acceptance tests (build verification)

AT-1 Happy path
- Given a small task that changes one function and updates a unit test, system converges to DONE within max_iterations.

AT-2 Test failure loop
- Introduce a task that intentionally breaks a test; system must detect failing tests, return REQUEST_CHANGES, fix, then pass.

AT-3 Reviewer failure
- Simulate reviewer CLI failure; controller retries then fails with clear logs.

AT-4 Stuck process
- Simulate a command that hangs; watchdog kills it and retries or fails per policy.

AT-5 Push failure
- Simulate auth failure; controller halts and produces patch artifact.

AT-6 Kill switch
- Create AUTOPILOT_STOP mid-run; controller halts at safe boundary and persists state.

AT-7 UAT generation + run
- Given a task with Acceptance Criteria, system generates UAT cases and runs configured UAT command(s) after review.

AT-8 UAT failure loop
- Given a task that passes unit tests but fails UAT, system must loop and converge or fail at max_iterations.

AT-9 Plan DAG scheduling
- Given a `plan.md` that expands into multiple dependent tasks, Codex produces a DAG and the orchestrator executes tasks in dependency order while parallelizing independent tasks up to `orchestrator.max_workers`.

## 17. Milestones

M1 Minimal viable loop (local)
- Task parsing
- State machine + persistence
- Planner invoke (Codex plan.md -> task DAG)
- Single-worker execution (Claude Code + Codex)
- Validation invoke
- Reviewer invoke (Codex; schema enforced)
- UAT generation (Codex) + UAT run (Claude Code)
- STATUS.md + logs
- Kill switch

M2 Robustness
- Timeouts + stuck detection
- Retry policies
- Diff cap + deny paths + TODO checks
- Push recovery + patch/bundle artifacts
- Multi-worker scheduling (task DAG parallelism)

M3 GitHub optional
- Push branch
- Optional PR open/update (via `gh` or API)
- Optional “final gate” script to sync release notes

M4 Polish
- Better console UX
- Task queue tooling
- Metrics summary (iterations per task, time)

## 18. Open questions (implementation decisions)

- Implementation language: Python (recommended for fast scripting and subprocess control) vs Node.
- Codex reviewer invocation: Codex CLI vs OpenAI API directly.
- PR creation method: `gh` CLI vs GitHub API.
- Repo-specific command discovery: prefer config.yml; optional fallback to parsing AGENTS.md.

End of document.
