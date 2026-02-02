"""Unit tests for ExecutionLoop stash strategy."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.executor.loop import ExecutionLoop
from src.config.models import AutopilotConfig, RepoConfig


@pytest.fixture
def mock_config():
    """Create a mock AutopilotConfig."""
    config = MagicMock()
    repo = MagicMock()
    repo.root = Path("/tmp/test_repo")
    repo.default_branch = "main"
    config.repo = repo
    config.safety = MagicMock()
    config.safety.allowed_paths = []
    return config


@pytest.fixture
def execution_loop(mock_config):
    """Create an ExecutionLoop instance with mocked dependencies."""
    with patch.multiple(
        "src.executor.loop",
        DAGScheduler=MagicMock(),
        OrchestratorMachine=MagicMock(),
        ValidationRunner=MagicMock(),
        ClaudeAgent=MagicMock(),
        CodexAgent=MagicMock(),
        GitHubIntegration=MagicMock(),
        SafetyChecker=MagicMock(),
        TerminalDashboard=MagicMock(),
        GitOps=MagicMock(),
    ):
        loop = ExecutionLoop(config=mock_config, resume=False, verbose=False)
        return loop


@pytest.mark.asyncio
async def test_stash_current_task_uat_creates_stash(execution_loop, tmp_path):
    """Test that _stash_current_task_uat creates a stash when UAT files exist."""
    repo_root = tmp_path
    task_id = "task-1"

    # Create UAT files
    (repo_root / "tests" / "uat").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "uat" / f"test_{task_id}_uat.py").write_text("# UAT test")
    (repo_root / "tests" / "uat" / f"{task_id}_uat.md").write_text("# UAT report")

    with patch.object(execution_loop, "_stash_current_task_uat") as mock_method:
        mock_method.return_value = True

        result = await execution_loop._stash_current_task_uat(repo_root, task_id)

        assert result is True
        mock_method.assert_called_once_with(repo_root, task_id)


@pytest.mark.asyncio
async def test_stash_current_task_uat_returns_false_when_no_files(execution_loop, tmp_path):
    """Test that _stash_current_task_uat returns False when no UAT files exist."""
    repo_root = tmp_path
    task_id = "task-1"

    # Don't create any UAT files
    (repo_root / "tests" / "uat").mkdir(parents=True, exist_ok=True)

    result = await execution_loop._stash_current_task_uat(repo_root, task_id)

    assert result is False


@pytest.mark.asyncio
async def test_drop_uat_stash_handles_missing_stash(execution_loop, tmp_path):
    """Test that _drop_uat_stash handles when stash doesn't exist."""
    repo_root = tmp_path
    task_id = "task-1"

    # Should not raise exception, just return early
    await execution_loop._drop_uat_stash(repo_root, task_id)


@pytest.mark.asyncio
async def test_merge_flow_with_stash(execution_loop, mock_config):
    """Test that merge flow integrates stash strategy correctly."""
    from unittest.mock import AsyncMock

    worktree_path = Path("/tmp/test_worktree")
    branch_name = "autopilot/task-1"
    task_id = "task-1"

    # Mock the subprocess manager
    mock_manager = MagicMock()
    mock_manager.run = AsyncMock()

    # Mock successful merge flow
    mock_manager.run.side_effect = [
        MagicMock(success=True, output="main"),  # rev-parse --abbrev-ref HEAD
        MagicMock(success=True, output=""),  # checkout main (already on main)
        MagicMock(success=True, output="Merge successful"),  # merge
    ]

    with patch("src.executor.loop.SubprocessManager", return_value=mock_manager):
        with patch.object(execution_loop, "_stash_current_task_uat", return_value=True) as mock_stash:
            with patch.object(execution_loop, "_drop_uat_stash", return_value=None) as mock_drop:
                await execution_loop._merge_worktree_to_main(
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                    allowed_paths=None,
                    task_id=task_id,
                )

                # Verify stash was called before merge
                mock_stash.assert_called_once_with(mock_config.repo.root, task_id)
                # Verify drop was called after successful merge
                mock_drop.assert_called_once_with(mock_config.repo.root, task_id)


@pytest.mark.asyncio
async def test_merge_flow_without_task_id(execution_loop, mock_config):
    """Test that merge flow works without task_id (backward compatibility)."""
    from unittest.mock import AsyncMock

    worktree_path = Path("/tmp/test_worktree")
    branch_name = "autopilot/task-1"

    # Mock the subprocess manager
    mock_manager = MagicMock()
    mock_manager.run = AsyncMock()

    # Mock successful merge flow
    mock_manager.run.side_effect = [
        MagicMock(success=True, output="main"),  # rev-parse --abbrev-ref HEAD
        MagicMock(success=True, output=""),  # checkout main (already on main)
        MagicMock(success=True, output="Merge successful"),  # merge
    ]

    with patch("src.executor.loop.SubprocessManager", return_value=mock_manager):
        with patch.object(execution_loop, "_stash_current_task_uat") as mock_stash:
            with patch.object(execution_loop, "_drop_uat_stash") as mock_drop:
                await execution_loop._merge_worktree_to_main(
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                    allowed_paths=None,
                    task_id=None,
                )

                # Verify stash/drop were NOT called when task_id is None
                mock_stash.assert_not_called()
                mock_drop.assert_not_called()


@pytest.mark.asyncio
async def test_merge_flow_handles_error_path(execution_loop, mock_config):
    """Test that merge flow handles error paths correctly with stash."""
    from unittest.mock import AsyncMock

    worktree_path = Path("/tmp/test_worktree")
    branch_name = "autopilot/task-1"
    task_id = "task-1"

    # Mock the subprocess manager
    mock_manager = MagicMock()
    mock_manager.run = AsyncMock()

    # Mock merge that initially fails but then succeeds with --autostash
    # This tests the path where stash should be cleaned up after successful merge
    mock_manager.run.side_effect = [
        MagicMock(success=True, output="main"),  # rev-parse --abbrev-ref HEAD
        MagicMock(success=True, output=""),  # checkout main (already on main)
        MagicMock(success=False, output="Your local changes to the following files would be overwritten by merge:\n\tsome_file.txt"),  # merge fails
        MagicMock(success=True, output="Merge successful"),  # autostash retry succeeds
    ]

    with patch("src.executor.loop.SubprocessManager", return_value=mock_manager):
        with patch.object(execution_loop, "_stash_current_task_uat", return_value=True) as mock_stash:
            with patch.object(execution_loop, "_drop_uat_stash") as mock_drop:
                await execution_loop._merge_worktree_to_main(
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                    allowed_paths=None,
                    task_id=task_id,
                )

                # Verify stash was called
                mock_stash.assert_called_once()
                # Verify drop WAS called because merge eventually succeeded
                mock_drop.assert_called_once()
