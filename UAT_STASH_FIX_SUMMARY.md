# UAT Merge Conflict Fix - Implementation Summary

## Problem

Sequential task execution failed with merge conflicts when UAT files from earlier tasks conflicted with later tasks.

**Error Example:**
```
error: Your local changes to the following files would be overwritten by merge:
  tests/uat/test_task_5_uat.py
```

**Root Cause:** UAT files are created in shared `tests/uat/` directory by each task. When task-2 creates a worktree from main (containing task-1's UAT files), then generates its own UAT files and attempts to merge back, git refuses to overwrite untracked files.

## Solution: Stash Strategy

Temporarily stash the current task's UAT files before merge, then drop the stash after successful merge. This preserves the task's UAT files in the worktree while allowing the merge to proceed cleanly.

## Implementation

### Files Modified

1. **`src/executor/loop.py`** - Main implementation
   - Added `_stash_current_task_uat()` helper method
   - Added `_drop_uat_stash()` helper method
   - Updated `_merge_worktree_to_main()` to accept `task_id` parameter
   - Integrated stash strategy into merge flow

2. **`tests/unit/test_executor_loop.py`** - Unit tests (NEW FILE)
   - Test stash creation when UAT files exist
   - Test stash returns False when no UAT files
   - Test stash drop after successful merge
   - Test merge flow integration with stash
   - Test backward compatibility (no task_id)
   - Test error handling and stash preservation

### Key Changes

#### 1. Stash Before Merge
```python
# Stash current task's UAT files before merge to prevent conflicts
# with UAT files from other tasks that may already be in main
if task_id:
    logger.info(f"Stashing UAT files for {task_id} before merge")
    await self._stash_current_task_uat(repo_root, task_id)
```

#### 2. Drop After Success
```python
# Drop the UAT stash after successful merge since files are now in main
if task_id:
    logger.info(f"Cleaning up UAT stash for {task_id}")
    await self._drop_uat_stash(repo_root, task_id)
```

#### 3. Preserve on Failure
All error paths now log a warning to indicate the stash is preserved for inspection:
```python
if task_id:
    logger.warning(f"Merge failed for {task_id}, UAT stash preserved for inspection")
```

### Helper Methods

#### `_stash_current_task_uat(repo_root, task_id) -> bool`
- Checks for UAT files: `tests/uat/test_{task_id}_uat.py` and `tests/uat/{task_id}_uat.md`
- Creates git stash with message: `autopilot-uat-{task_id}`
- Returns `True` if stash created, `False` if no UAT files exist

#### `_drop_uat_stash(repo_root, task_id) -> None`
- Lists git stashes to find the one created by `_stash_current_task_uat`
- Drops the stash after successful merge
- Safe to call even if stash doesn't exist

## Testing

### Unit Tests (6 tests, all passing)
- `test_stash_current_task_uat_creates_stash` - Verifies stash creation
- `test_stash_current_task_uat_returns_false_when_no_files` - No-op when no UAT files
- `test_drop_uat_stash_handles_missing_stash` - Handles missing stash gracefully
- `test_merge_flow_with_stash` - Full integration test
- `test_merge_flow_without_task_id` - Backward compatibility
- `test_merge_flow_handles_error_path` - Error handling

### Test Results
```
tests/unit/test_executor_loop.py::test_stash_current_task_uat_creates_stash PASSED
tests/unit/test_executor_loop.py::test_stash_current_task_uat_returns_false_when_no_files PASSED
tests/unit/test_executor_loop.py::test_drop_uat_stash_handles_missing_stash PASSED
tests/unit/test_executor_loop.py::test_merge_flow_with_stash PASSED
tests/unit/test_executor_loop.py::test_merge_flow_without_task_id PASSED
tests/unit/test_executor_loop.py::test_merge_flow_handles_error_path PASSED
=========================== 6 passed ===========================
```

All 111 unit tests in the project pass.

## Backward Compatibility

**No Breaking Changes:**
- `task_id` parameter is optional in `_merge_worktree_to_main()`
- Existing workflows without UAT files are unaffected
- Stash strategy only activates when `task_id` is provided

## Verification Checklist

- [x] Unit tests pass (6/6)
- [x] All unit tests pass (111/111)
- [x] Backward compatibility preserved
- [x] Error handling includes stash preservation warnings
- [ ] Integration test with 2 sequential tasks (to be added)
- [ ] Full smoke test (15 tasks) (to be added)

## Next Steps

1. **Integration Testing:** Create a 2-task sequential smoke test to verify end-to-end functionality
2. **Manual Testing:** Run full smoke test with 15 tasks to ensure no merge conflicts
3. **Documentation:** Update user documentation if needed

## Rollback Plan

If issues arise:
1. Revert changes to `src/executor/loop.py`
2. Remove `tests/unit/test_executor_loop.py`
3. Previous merge behavior will be restored
4. Alternative: Add `tests/uat/` to `_AUTO_RESOLVE_CONFLICT_PREFIXES` (less safe)
