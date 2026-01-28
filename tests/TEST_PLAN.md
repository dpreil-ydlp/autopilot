# Autopilot Acceptance & System Test Plan

This test plan defines the minimum pass criteria for Autopilot to be considered complete. It covers functional behavior, robustness, recovery, scheduling, and operator UX.

## 1) Core functional flow (single task)

T-001 Happy path end-to-end  
Given a small task (one function change + unit test), the system completes: PLAN → BUILD → VALIDATE → REVIEW → UAT_GENERATE → UAT_RUN → DONE, with PASS.  
Expected: tests pass, reviewer APPROVE, UAT pass, commit created.

T-002 Task parsing  
Given a valid `tasks/<date>_<slug>.md`, the controller parses Goal/Acceptance Criteria/Constraints/Allowed Paths/Validation Commands/UAT.  
Expected: parsed fields appear in logs and prompts; invalid format fails with actionable error.

T-003 Validation command overrides  
Given task-level overrides, the system uses task-specific commands rather than config defaults.  
Expected: validation uses overrides; logs record exact command.

T-004 Reviewer schema enforcement  
Given a malformed reviewer response, the controller rejects it and retries; after max retries, fails with logs.  
Expected: strict JSON schema enforcement.

T-005 Builder summary enforcement  
Given a builder response without parseable JSON summary, controller retries with “return JSON only” and fails after retry exhaustion.  
Expected: summary stored in `.autopilot/logs/`.

## 2) Codex planning + task DAG

T-010 plan.md expansion  
Given a `plan.md`, Codex produces a plan DAG with dependencies, ready set, and suggested skills/subagents/MCP.  
Expected: `.autopilot/plan/` populated; per-task MD files created.

T-011 DAG correctness (ordering)  
Given a plan with explicit dependencies, tasks do not start until dependencies are DONE.  
Expected: scheduler enforces `depends_on`.

T-012 DAG ready-set parallelism  
Given independent tasks, system dispatches them concurrently (up to `orchestrator.max_workers`).  
Expected: multiple workers active; terminal shows parallel tasks.

T-013 Task status tracking  
Given tasks moving through states, per-task MD status updates reflect PENDING/RUNNING/DONE/BLOCKED/FAILED.  
Expected: status transitions recorded reliably.

T-014 Per-task guidance propagation  
Codex provides suggested skills/subagents/MCP; Claude receives this in prompts and reports usage.  
Expected: builder summary includes `skills_used`, `mcp_servers_used`, `subagents_used`.

## 3) UAT lifecycle

T-020 UAT generation (Codex)  
Codex generates UAT cases from acceptance criteria and diff.  
Expected: `.autopilot/uat/<task_id>_uat.md` created.

T-021 UAT run (Claude)  
Claude runs `commands.uat` and captures logs.  
Expected: exit codes logged; results surfaced in status.

T-022 UAT failure loop  
Given failing UAT, system iterates (build → review → UAT) until pass or max iterations.  
Expected: loop stops only when pass or cap exceeded.

T-023 UAT skipped behavior  
If `commands.uat` missing, UAT recorded as skipped and `uat_pass=true`.  
Expected: explicit note in STATUS.md.

## 4) Review + validation failure handling

T-030 Test failure loop  
Given failing tests, reviewer MUST request changes; Claude fixes; loop continues.  
Expected: no progression to DONE without passing tests.

T-031 Review failure loop  
Given reviewer REQUEST_CHANGES, system loops and applies fixes.  
Expected: DECIDE blocks commit until APPROVE.

T-032 Mixed failures  
If tests pass but review fails (or UAT fails), system loops appropriately.  
Expected: gating consistent with DECIDE rules.

## 5) Robustness & retries

T-040 Planner CLI failure  
Planner non-zero exit triggers retry then FAIL.  
Expected: logs captured, state marked FAILED if retries exhausted.

T-041 Builder CLI failure  
Builder non-zero exit triggers retry then FAIL.  
Expected: correct retry policy.

T-042 Reviewer CLI failure  
Reviewer non-zero exit triggers retry then FAIL.  
Expected: correct retry policy.

T-043 Stuck detection  
Simulate no output longer than `stuck_no_output_sec`.  
Expected: process killed and retried per policy.

## 6) Scheduling + multi-worker conflicts

T-050 Worker pool limit  
Given many ready tasks, concurrent workers never exceed `orchestrator.max_workers`.  
Expected: enforced cap.

T-051 File conflict mitigation  
Parallel tasks touching same file should be serialized by planner or recovered by conflict handling.  
Expected: no silent overwrite; conflicts resolved or task fails with actionable logs.

T-052 Merge/rebase conflict recovery  
Introduce conflict during merge from worker branch.  
Expected: Claude receives conflict markers; retry loop; resolves or fails after cap.

## 7) Crash, restart, and recovery

T-060 Crash consistency  
Simulate abrupt termination during state update.  
Expected: state.json remains valid; resumes from last safe boundary.

T-061 Resume with RUNNING tasks  
On restart, tasks previously RUNNING are re-queued (or failed if attempts exceeded).  
Expected: scheduler recomputes ready set and continues.

T-062 Dirty workspace recovery  
On resume with dirty tree, system creates snapshot artifacts and either auto-stashes or fails with clear instructions.  
Expected: no silent data loss.

T-063 Lock enforcement  
Attempt to start a second run while lock is held.  
Expected: second run fails fast with a clear message.

## 8) Git + artifacts

T-070 Commit behavior  
On DONE, a commit is created on the task branch.  
Expected: commit SHA recorded in state.

T-071 Push failures  
Simulate auth / non-fast-forward / network issues.  
Expected: classification + retries + patch/bundle artifacts.

T-072 Artifact creation  
On failure, patch/bundle artifacts generated.  
Expected: paths logged in STATUS.md.

## 9) Terminal UX / operator feedback

T-080 Live dashboard  
During a run, terminal displays orchestrator state, DAG counts, and per-worker lines.  
Expected: visible progress without opening files.

T-081 Sub-agent visibility  
When Claude spawns sub-agents, console explicitly notes it.  
Expected: clear operator feedback.

T-082 Verbosity flags  
`--quiet` reduces output to state transitions; `--verbose` streams step output.  
Expected: output matches flag.

T-083 status --watch  
Continuous refresh shows live updates during run.  
Expected: refresh works and is readable.

## 10) Security + guardrails

T-090 Allowed/deny path enforcement  
Changes outside allowed paths are blocked.  
Expected: task fails with clear violation report.

T-091 Diff line cap  
Large diff triggers cap and fails.  
Expected: safe halt with guidance.

T-092 No new TODO/FIXME  
Introduced TODO/FIXME causes failure if `forbid_todos` enabled.  
Expected: flagged in review or guardrail step.

## 11) Plan DAG artifacts

T-100 Task graph artifact integrity  
Plan JSON + task graph files present and parseable.  
Expected: DAG can be reloaded.

T-101 Per-task MD format  
Generated task files include `id`, `status`, `depends_on`, `attempts`, and optional `assigned_worker`.  
Expected: consistent parsing.

---

## Completion criteria

Autopilot is considered complete when all tests above pass, or are explicitly marked “not applicable” with justification, and the system can resume reliably after forced interruption.
