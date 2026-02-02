# Phase 4: Live Smoke Test Plan for Autopilot

**Date:** 2026-02-01
**Target:** `/Users/davidpreil/Projects/business_finder`
**Autopilot Version:** 0.1.45
**Goal:** Validate end-to-end Autopilot functionality with 10-20 deliberate test tasks

## Test Objectives

This smoke test deliberately exercises ALL critical Autopilot components:

1. ✅ Task parsing + plan materialization
2. ✅ Multi-worker dispatch (≥ 4 workers)
3. ✅ Per-task scoping rules (allowed paths restrictions + out-of-scope protection)
4. ✅ Builder → validator → reviewer handoff loop
5. ✅ UAT generation and UAT execution
6. ✅ Lint gate that touches `tests/uat/` (with auto-fix)
7. ✅ Worktree merge/handback under constrained allowed paths
8. ✅ Recovery behavior (safe cleanup paths, worktree cleanup)
9. ✅ Status reporting and state persistence correctness
10. ✅ MCP usage in builder (for MCP-capable builder modes)

## Acceptance Criteria

**ALL of these MUST pass:**
- ✅ 0 tasks failed due to linting generated UAT files
- ✅ 0 tasks failed at worktree merge due to missing-path checkout
- ✅ No "diff failed to apply but working tree has changes; continuing" unless proven in-scope
- ✅ `autopilot status` reflects actual scheduler state while running and after completion
- ✅ 100% task completion rate (all 15 tasks complete)

## Test Plan Structure

### Task Categories (15 total tasks)

**A. Simple Source File Modifications (5 tasks)**
- Each task modifies a distinct small file in `src/`
- Tests: scope enforcement, file modification, patch apply correctness
- Parallelizable: Yes (after task 1)

**B. Test File Additions (5 tasks)**
- Each task adds a small test file in `tests/`
- Tests: test creation, UAT generation, validation gates
- Parallelizable: Yes (after task 1)

**C. UAT Hygiene Testing (2 tasks)**
- Tasks that intentionally generate UAT with minor fixable lint issues
- Tests: auto-fix ruff integration, lint gate recovery
- Parallelizable: Yes

**D. Parallel Merge Testing (2 tasks)**
- Tasks running in parallel touching adjacent paths
- Tests: worktree merge, conflict resolution, handback
- Parallelizable: Yes (intentionally adjacent)

**E. MCP Integration Testing (0 tasks)**
- NOTE: business_finder config has `disable_mcp: true` for reviewer
- MCP testing deferred to MCP-enabled repos

---

## Detailed Task Specifications

### Phase 1: Foundation (Sequential - Tasks 1-2)

#### Task 1: Create Base Utilities Module
**Complexity:** Low
**Allowed Paths:** `src/lib/`

**Goal:** Create a base utility module with helper functions for business data validation.

**Description:**
Create `src/lib/utils/validation.ts` with the following:
- Function `isValidEmail(email: string): boolean` - validates email format
- Function `isValidPhone(phone: string): boolean` - validates US phone format
- Function `isValidBusinessName(name: string): boolean` - non-empty, min 3 chars
- Proper TypeScript types and JSDoc comments
- Export all functions

**Dependencies:** None

**Acceptance Criteria:**
- [ ] File `src/lib/utils/validation.ts` exists
- [ ] All three functions are implemented with correct logic
- [ ] Functions have proper TypeScript signatures
- [ ] JSDoc comments present for each function
- [ ] File compiles without TypeScript errors
- [ ] Code follows project conventions

**Constraints:**
- Follow existing code patterns in `src/lib/`
- Use regex for email validation (standard pattern)
- Use regex for phone validation (US format)
- No external dependencies

---

#### Task 2: Create Base Test Infrastructure
**Complexity:** Low
**Allowed Paths:** `tests/unit/`

**Goal:** Create base test utilities and fixtures for unit testing.

**Description:**
Create `tests/unit/fixtures/business_data.ts` with the following:
- Valid business data fixture (name, email, phone, address)
- Invalid business data fixture (missing fields, bad formats)
- Edge case fixtures (empty strings, special characters, unicode)
- Export all fixtures as named exports

**Dependencies:** Task 1

**Acceptance Criteria:**
- [ ] File `tests/unit/fixtures/business_data.ts` exists
- [ ] At least 5 valid fixtures defined
- [ ] At least 5 invalid fixtures defined
- [ ] Fixtures cover edge cases (empty, special chars, unicode)
- [ ] All fixtures properly typed with TypeScript interfaces
- [ ] File compiles without errors

**Constraints:**
- Use TypeScript interfaces for type safety
- Follow existing test patterns in `tests/`
- No external test dependencies (use standard Jest/Vitest patterns)

---

### Phase 2: Parallel Source Development (Tasks 3-7)

#### Task 3: Create Business Type Definitions
**Complexity:** Low
**Allowed Paths:** `src/types/`

**Goal:** Create TypeScript type definitions for business entities.

**Description:**
Create `src/types/business.ts` with the following:
- Interface `Business` with fields: id, name, email, phone, address, category, rating
- Interface `BusinessCategory` with fields: id, name, description
- Interface `BusinessSearchResult` with fields: businesses, total, page
- Export all interfaces
- Add JSDoc comments

**Dependencies:** Task 1

**Acceptance Criteria:**
- [ ] File `src/types/business.ts` exists
- [ ] All three interfaces defined with correct fields
- [ ] Fields have appropriate types (string, number, boolean, etc.)
- [ ] Optional fields marked with `?`
- [ ] All interfaces exported
- [ ] JSDoc comments present

**Constraints:**
- Follow existing type patterns in `src/types/`
- Use standard TypeScript types
- No external dependencies

---

#### Task 4: Create Business Store Module
**Complexity:** Medium
**Allowed Paths:** `src/stores/`

**Goal:** Create a Zustand store for business data management.

**Description:**
Create `src/stores/businessStore.ts` with the following:
- State interface with: businesses, loading, error, selectedBusiness
- Actions: fetchBusinesses, addBusiness, updateBusiness, deleteBusiness, selectBusiness
- Use Zustand for state management
- Proper TypeScript typing throughout
- Async actions with error handling

**Dependencies:** Task 3

**Acceptance Criteria:**
- [ ] File `src/stores/businessStore.ts` exists
- [ ] Zustand store created with proper state interface
- [ ] All 5 actions implemented
- [ ] Actions properly typed
- [ ] Error handling in async actions
- [ ] Follows existing store patterns in `src/stores/`

**Constraints:**
- Use Zustand (already in project dependencies)
- Mock API calls (no real backend integration yet)
- Follow existing store patterns

---

#### Task 5: Create Business Card Component
**Complexity:** Medium
**Allowed Paths:** `src/components/`

**Goal:** Create a reusable business card component for displaying business information.

**Description:**
Create `src/components/BusinessCard.tsx` with the following:
- Functional component accepting Business prop
- Display: name, category, rating, address (formatted)
- Tailwind CSS for styling (responsive design)
- Click handler prop for onBusinessClick
- Proper TypeScript props interface
- Loading and error states (optional props)

**Dependencies:** Task 3

**Acceptance Criteria:**
- [ ] File `src/components/BusinessCard.tsx` exists
- [ ] Component accepts Business prop
- [ ] Displays all required business information
- [ ] Responsive design with Tailwind CSS
- [ ] Click handler works correctly
- [ ] Props interface properly defined
- [ ] Component renders without errors

**Constraints:**
- Use Tailwind CSS (already configured)
- Follow existing component patterns in `src/components/`
- Keep component simple and focused

---

#### Task 6: Create Business Search Component
**Complexity:** Medium
**Allowed Paths:** `src/components/`

**Goal:** Create a search input component for filtering businesses.

**Description:**
Create `src/components/BusinessSearch.tsx` with the following:
- Functional component with search input and filters
- Props: onSearch callback, initialSearchValue string
- Filter by: name, category, rating
- Debounced search input (300ms delay)
- Reset button to clear filters
- Tailwind CSS styling
- Proper TypeScript typing

**Dependencies:** Task 3

**Acceptance Criteria:**
- [ ] File `src/components/BusinessSearch.tsx` exists
- [ ] Search input accepts text
- [ ] Filter dropdowns work (name, category, rating)
- [ ] Debounce implemented (300ms)
- [ ] Reset button clears all filters
- [ ] Proper TypeScript types
- [ ] Follows component patterns

**Constraints:**
- Use React hooks (useState, useCallback, useMemo)
- Debounce implementation (custom or lodash)
- Tailwind CSS styling

---

#### Task 7: Create Business List Page
**Complexity:** Medium
**Allowed Paths:** `src/pages/`

**Goal:** Create a page component that displays a list of businesses with search.

**Description:**
Create `src/pages/BusinessList.tsx` with the following:
- Uses BusinessSearch and BusinessCard components
- Displays paginated list of businesses (10 per page)
- Loading and error states
- Pagination controls (prev, next, page numbers)
- Uses businessStore for data
- Responsive layout (Tailwind CSS)
- Proper TypeScript typing

**Dependencies:** Task 4, Task 5, Task 6

**Acceptance Criteria:**
- [ ] File `src/pages/BusinessList.tsx` exists
- [ ] BusinessSearch component integrated
- [ ] BusinessCard components rendered in list
- [ ] Pagination works (10 per page)
- [ ] Loading state shows spinner/skeleton
- [ ] Error state shows error message
- [ ] Responsive layout
- [ ] Uses businessStore

**Constraints:**
- Use existing components (Tasks 5, 6)
- Use businessStore (Task 4)
- Tailwind CSS styling
- Follow page patterns in `src/pages/`

---

### Phase 3: Test Development (Tasks 8-12)

#### Task 8: Create Validation Utils Tests
**Complexity:** Low
**Allowed Paths:** `tests/unit/`

**Goal:** Create unit tests for validation utility functions.

**Description:**
Create `tests/unit/validation.test.ts` with the following:
- Test suite for `isValidEmail` function (5+ cases)
- Test suite for `isValidPhone` function (5+ cases)
- Test suite for `isValidBusinessName` function (5+ cases)
- Test edge cases: empty strings, null, undefined, special characters
- Use test fixtures from Task 2
- 100% coverage of validation functions

**Dependencies:** Task 1, Task 2

**Acceptance Criteria:**
- [ ] File `tests/unit/validation.test.ts` exists
- [ ] At least 15 test cases total
- [ ] All edge cases covered
- [ ] Uses fixtures from Task 2
- [ ] All tests pass
- [ ] Tests follow project conventions

**Constraints:**
- Use project's test framework (Vitest/Jest)
- Use fixtures from Task 2
- Follow test naming conventions

---

#### Task 9: Create Business Store Tests
**Complexity:** Medium
**Allowed Paths:** `tests/unit/`

**Goal:** Create unit tests for business store functionality.

**Description:**
Create `tests/unit/businessStore.test.ts` with the following:
- Test store initialization
- Test fetchBusinesses action
- Test addBusiness action
- Test updateBusiness action
- Test deleteBusiness action
- Test selectBusiness action
- Test error handling in async actions
- Mock API responses

**Dependencies:** Task 4

**Acceptance Criteria:**
- [ ] File `tests/unit/businessStore.test.ts` exists
- [ ] All store actions tested
- [ ] Error handling tested
- [ ] API calls mocked
- [ ] All tests pass
- [ ] Tests follow conventions

**Constraints:**
- Mock async actions
- Test both success and error cases
- Follow test patterns in `tests/`

---

#### Task 10: Create Business Card Component Tests
**Complexity:** Medium
**Allowed Paths:** `tests/unit/`

**Goal:** Create unit tests for BusinessCard component.

**Description:**
Create `tests/unit/BusinessCard.test.tsx` with the following:
- Test component renders correctly
- Test all business fields display
- Test click handler fires
- Test loading state (if implemented)
- Test error state (if implemented)
- Use React Testing Library
- Mock business data

**Dependencies:** Task 5

**Acceptance Criteria:**
- [ ] File `tests/unit/BusinessCard.test.tsx` exists
- [ ] Component rendering tested
- [ ] All fields display tested
- [ ] Click handler tested
- [ ] Loading/error states tested (if applicable)
- [ ] Uses React Testing Library
- [ ] All tests pass

**Constraints:**
- Use React Testing Library
- Test user interactions, not implementation
- Follow test patterns

---

#### Task 11: Create Business Search Component Tests
**Complexity:** Medium
**Allowed Paths:** `tests/unit/`

**Goal:** Create unit tests for BusinessSearch component.

**Description:**
Create `tests/unit/BusinessSearch.test.tsx` with the following:
- Test search input renders and accepts text
- Test filter dropdowns work
- Test debounce delay (use fake timers)
- Test reset button clears filters
- Test onSearch callback fires correctly
- Use React Testing Library

**Dependencies:** Task 6

**Acceptance Criteria:**
- [ ] File `tests/unit/BusinessSearch.test.tsx` exists
- [ ] Search input tested
- [ ] Filters tested
- [ ] Debounce tested with fake timers
- [ ] Reset button tested
- [ ] Callback tested
- [ ] All tests pass

**Constraints:**
- Use fake timers for debounce testing
- Test user behavior, not implementation
- Follow test patterns

---

#### Task 12: Create Business List Page Tests
**Complexity:** Medium
**Allowed Paths:** `tests/unit/`

**Goal:** Create integration tests for BusinessList page.

**Description:**
Create `tests/unit/BusinessList.test.tsx` with the following:
- Test page renders correctly
- Test BusinessSearch integration
- Test BusinessCard list rendering
- Test pagination controls
- Test loading state
- Test error state
- Test store integration
- Use React Testing Library

**Dependencies:** Task 7, Task 9

**Acceptance Criteria:**
- [ ] File `tests/unit/BusinessList.test.tsx` exists
- [ ] Page rendering tested
- [ ] Component integration tested
- [ ] Pagination tested
- [ ] States (loading, error) tested
- [ ] Store integration tested
- [ ] All tests pass

**Constraints:**
- Integration test (tests multiple components)
- Mock store state
- Test user workflows

---

### Phase 4: UAT Hygiene & Parallel Testing (Tasks 13-15)

#### Task 13: Create API Service Module (Parallel Test A)
**Complexity:** Medium
**Allowed Paths:** `src/lib/api/`

**Goal:** Create an API service module for business-related API calls.

**Description:**
Create `src/lib/api/businessApi.ts` with the following:
- Function `fetchBusinesses(params)` - get paginated businesses
- Function `fetchBusinessById(id)` - get single business
- Function `createBusiness(data)` - create new business
- Function `updateBusiness(id, data)` - update business
- Function `deleteBusiness(id)` - delete business
- Proper TypeScript types
- Error handling
- Mock implementation (returns fake data)

**Dependencies:** Task 3

**Acceptance Criteria:**
- [ ] File `src/lib/api/businessApi.ts` exists
- [ ] All 5 functions implemented
- [ ] Proper TypeScript types
- [ ] Error handling in place
- [ ] Mock implementation returns realistic data
- [ ] Functions follow REST conventions
- [ ] Code compiles without errors

**Constraints:**
- Mock implementation (no real HTTP calls)
- Use async/await
- Return typed responses

**Note for Builder:**
This task will be run in parallel with Task 14 to test merge behavior. Both tasks may touch adjacent paths in `src/lib/`.

---

#### Task 14: Create API Client Utilities (Parallel Test B)
**Complexity:** Medium
**Allowed Paths:** `src/lib/api/`

**Goal:** Create API client utility functions for HTTP requests.

**Description:**
Create `src/lib/api/client.ts` with the following:
- Function `get(url, params)` - GET request
- Function `post(url, data)` - POST request
- Function `put(url, data)` - PUT request
- Function `delete(url)` - DELETE request
- Function `buildQuery(params)` - build query string
- Error handling wrapper
- TypeScript types

**Dependencies:** Task 3

**Acceptance Criteria:**
- [ ] File `src/lib/api/client.ts` exists
- [ ] All 5 functions implemented
- [ ] buildQuery creates proper query strings
- [ ] Error handling wrapper
- [ ] Proper TypeScript types
- [ ] Mock implementation (no real HTTP)
- [ ] Code compiles without errors

**Constraints:**
- Mock implementation
- Use standard fetch API signature
- Return typed responses

**Note for Builder:**
This task will be run in parallel with Task 13 to test merge behavior. Both tasks touch `src/lib/api/`. This tests Autopilot's worktree merge and conflict resolution.

---

#### Task 15: Create Integration Test Suite (UAT Hygiene Test)
**Complexity:** Medium
**Allowed Paths:** `tests/integration/`

**Goal:** Create integration test suite for business workflow.

**Description:**
Create `tests/integration/business-workflow.test.ts` with the following:
- Test end-to-end business search workflow
- Test business creation workflow
- Test business update workflow
- Test business deletion workflow
- Use multiple components together
- Mock all external dependencies
- Test user workflows, not implementation

**Dependencies:** Task 7, Task 12

**Acceptance Criteria:**
- [ ] File `tests/integration/business-workflow.test.ts` exists
- [ ] At least 4 workflow tests
- [ ] Tests cover CRUD operations
- [ ] External dependencies mocked
- [ ] Tests follow project conventions
- [ ] All tests pass

**Constraints:**
- Integration test (tests multiple units together)
- Mock external dependencies
- Focus on user workflows

**Note for Builder:**
This task will generate UAT tests. The builder should create clean, lint-free UAT code. If lint issues are found, Autopilot's auto-fix should handle them.

---

## Dependency Graph

```
Task 1 (validation utils) → Task 3 (types)
                           ↓
Task 2 (test fixtures) → Task 8 (validation tests)
                           ↓
Task 3 (types) → Task 4 (store) → Task 7 (page) → Task 12 (page tests)
              ↓                ↓
              Task 5 (card) → Task 10 (card tests)
              ↓
              Task 6 (search) → Task 11 (search tests)
                                   ↓
Task 13 (API module) ←┐
Task 14 (API client) ←┤ (parallel merge test)
                      ↓
Task 15 (integration tests)
```

**Parallel Batches:**
- Batch 1: Task 1, Task 2
- Batch 2: Task 3
- Batch 3: Task 4, Task 5, Task 6
- Batch 4: Task 7, Task 8
- Batch 5: Task 9, Task 10, Task 11
- Batch 6: Task 12
- Batch 7: Task 13, Task 14 (parallel merge test)
- Batch 8: Task 15

## Expected Behavior

### During Execution:
1. Autopilot expands this plan into 15 task files
2. Scheduler dispatches tasks to workers respecting dependencies
3. Each task executes: Builder → Validator → Reviewer → (if needed) UAT
4. Multi-workers process parallelizable tasks concurrently
5. State machine transitions: INIT → PRECHECK → PLAN → SCHEDULE → DISPATCH → MONITOR → FINAL_UAT_RUN → DONE

### Validation Gates:
- **Lint:** `ruff check .` (includes `tests/uat/` with auto-fix)
- **Tests:** `pytest -q` (includes `tests/uat/`)
- **UAT:** `pytest -q tests/uat`

### Success Indicators:
- ✅ All 15 tasks complete
- ✅ 0 lint failures (auto-fix handles any UAT issues)
- ✅ All tests pass (unit + integration + UAT)
- ✅ Clean worktree merge for Tasks 13/14
- ✅ State persistence accurate throughout
- ✅ Status reporting matches actual state

---

## Execution Instructions

### First Run:
```bash
cd /Users/davidpreil/Projects/business_finder
autopilot run phase4_smoke_test_plan.md
```

### Verify Success:
```bash
# Check status
autopilot status

# Check state
cat .autopilot/state.json | jq .

# Check logs
tail -100 .autopilot/logs/autopilot_*.log

# Verify all files created
ls -la src/lib/utils/validation.ts
ls -la src/types/business.ts
ls -la src/components/BusinessCard.tsx
ls -la tests/unit/validation.test.ts

# Run lint manually
ruff check .

# Run tests manually
pytest -q
```

### Second Run (Cache & Stability Test):
```bash
# Clean state but keep cache
autopilot clean
autopilot run phase4_smoke_test_plan.md
```

The second run should:
- Be faster (cache warm)
- Have no new failures
- Handle existing artifacts gracefully

---

## Known Issues to Monitor

Based on Phase 1-3 findings:

1. **Lint failures in UAT files** → Auto-fix should handle (see `validation/runner.py:384-417`)
2. **Worktree merge issues** → Tasks 13/14 will test adjacent-path merges
3. **State drift** → Monitor `autopilot status` vs actual scheduler activity
4. **Patch apply false positives** → Verify no "diff failed but continuing" unless proven

---

## Success Metrics

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Task Completion Rate | 100% (15/15) | `autopilot status` |
| Lint Failures | 0 | `ruff check .` |
| Test Failures | 0 | `pytest -q` |
| UAT Failures | 0 | `pytest -q tests/uat` |
| Merge Failures | 0 | Check logs for "merge failed" |
| State Accuracy | 100% | Compare `state.json` with actual |
| Second Run Success | 100% | Run twice, compare results |

---

## Post-Run Verification Checklist

- [ ] All 15 task files exist in `.autopilot/plan/tasks/`
- [ ] All source files created in `src/`
- [ ] All test files created in `tests/`
- [ ] No merge conflicts in git history
- [ ] Clean git state (no uncommitted changes in worktrees)
- [ ] Worktrees properly cleaned up
- [ ] State file shows `DONE` status
- [ ] STATUS.md matches final state
- [ ] Logs show clean execution (no retry storms)
- [ ] Second run completes without new issues

---

**This plan is deliberately comprehensive to stress-test all Autopilot components.**
**Success here confirms the system is production-ready.**
