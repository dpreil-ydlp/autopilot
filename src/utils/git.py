"""Git operations wrapper."""

import logging
import re
import shutil
from pathlib import Path

from .subprocess import SubprocessError, SubprocessManager

logger = logging.getLogger(__name__)


class GitError(Exception):
    """Git operation error."""

    pass


class GitOps:
    """Git operations wrapper."""

    def __init__(self, repo_root: Path, timeout_sec: int = 30):
        """Initialize Git operations.

        Args:
            repo_root: Repository root directory
            timeout_sec: Default timeout for operations
        """
        self.repo_root = repo_root
        self.timeout_sec = timeout_sec
        self.manager = SubprocessManager(timeout_sec=timeout_sec)

    async def run_git(self, args: list[str], check: bool = True) -> dict:
        """Run git command.

        Args:
            args: Git arguments
            check: Whether to check exit code

        Returns:
            Result dict

        Raises:
            GitError: On failure
        """
        command = ["git"] + args
        try:
            result = await self.manager.run(command, cwd=self.repo_root)

            if check and not result["success"]:
                raise GitError(f"Git command failed: {' '.join(args)}\n{result['output']}")

            return result

        except SubprocessError as e:
            raise GitError(f"Git subprocess error: {e}")

    async def get_current_branch(self) -> str:
        """Get current branch name.

        Returns:
            Branch name
        """
        result = await self.run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        return result["output"].strip()

    async def get_original_branch(self) -> str:
        """Get the branch we started from before any changes.

        Returns:
            Original branch name
        """
        # Check if we're in a detached HEAD or worktree
        result = await self.run_git(["branch", "--show-current"])
        branch = result["output"].strip()

        if not branch:
            # Detached HEAD, try to get the ref
            result = await self.run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            branch = result["output"].strip()

        return branch or "main"

    async def create_branch(self, branch_name: str, start_point: str | None = None) -> None:
        """Create new branch.

        Args:
            branch_name: New branch name
            start_point: Starting point (defaults to current HEAD)
        """
        await self.ensure_initialized()
        args = ["checkout", "-b", branch_name]
        if start_point:
            args.append(start_point)

        await self.run_git(args)
        logger.info(f"Created branch: {branch_name}")

    async def checkout(self, ref: str) -> None:
        """Checkout branch or commit.

        Args:
            ref: Branch name or commit hash
        """
        await self.run_git(["checkout", ref])
        logger.info(f"Checked out: {ref}")

    async def get_diff(self, cached: bool = False, paths: list[str] | None = None) -> str:
        """Get git diff.

        Args:
            cached: Show staged changes
            paths: Specific paths to diff

        Returns:
            Diff output
        """
        args = ["diff"]
        if cached:
            args.append("--cached")

        if paths:
            args.extend(paths)

        result = await self.run_git(args)
        return result["output"]

    async def commit(
        self,
        message: str,
        allow_empty: bool = False,
    ) -> str:
        """Commit changes.

        Args:
            message: Commit message
            allow_empty: Allow empty commit

        Returns:
            Commit hash
        """
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")

        await self.run_git(args)

        # Get commit hash
        result = await self.run_git(["rev-parse", "HEAD"])
        commit_hash = result["output"].strip()
        logger.info(f"Committed: {commit_hash[:8]} - {message.split(chr(10))[0]}")

        return commit_hash

    async def add(self, paths: list[str]) -> None:
        """Stage files.

        Args:
            paths: File paths to stage
        """
        await self.run_git(["add"] + paths)
        logger.info(f"Staged {len(paths)} files")

    async def push(
        self,
        remote: str,
        branch: str,
        force: bool = False,
        retry: int = 2,
    ) -> dict:
        """Push branch to remote.

        Args:
            remote: Remote name
            branch: Branch name
            force: Force push
            retry: Number of retries on failure

        Returns:
            Result dict with success status

        Raises:
            GitError: On unrecoverable failure
        """
        args = ["push", remote, branch]
        if force:
            args.append("--force")

        retries = 0
        last_error = None

        while retries <= retry:
            try:
                result = await self.run_git(args, check=False)

                if result["success"]:
                    logger.info(f"Pushed {branch} to {remote}")
                    return {"success": True, "output": result["output"]}

                # Analyze failure
                error_output = result["output"].lower()
                if "auth" in error_output or "credential" in error_output:
                    # Auth failure - check with gh CLI
                    raise GitError("Push failed: Authentication error")
                elif "non-fast-forward" in error_output:
                    # Need to pull/rebase first
                    logger.warning("Push failed: non-fast-forward. Fetching and rebasing...")
                    await self.run_git(["fetch", remote])
                    await self.run_git(["rebase", f"{remote}/{branch}"])
                    # Retry push
                    retries += 1
                    last_error = "non-fast-forward"
                    continue
                elif "network" in error_output or "connection" in error_output:
                    # Network error - retry
                    retries += 1
                    last_error = "network"
                    logger.warning(f"Push failed: network error (retry {retries}/{retry})")
                    continue
                else:
                    # Other error
                    raise GitError(f"Push failed: {result['output']}")

            except GitError:
                raise
            except Exception as e:
                retries += 1
                last_error = str(e)
                logger.warning(f"Push failed: {e} (retry {retries}/{retry})")

        # All retries exhausted
        raise GitError(f"Push failed after {retry} retries: {last_error}")

    async def create_patch(self, output_path: Path) -> None:
        """Create patch file with current changes.

        Args:
            output_path: Output patch file path
        """
        await self.run_git(
            ["diff", "--binary", "--output", str(output_path)],
            check=False,
        )
        logger.info(f"Created patch: {output_path}")

    async def create_bundle(self, output_path: Path, branch: str) -> None:
        """Create git bundle file.

        Args:
            output_path: Output bundle file path
            branch: Branch to bundle
        """
        await self.run_git(["bundle", "create", str(output_path), branch])
        logger.info(f"Created bundle: {output_path}")

    async def list_files_changed(self) -> list[str]:
        """List changed files.

        Returns:
            List of changed file paths
        """
        result = await self.run_git(["diff", "--name-only"])
        files = [f.strip() for f in result["output"].split("\n") if f.strip()]
        return files

    async def count_lines_changed(self) -> int:
        """Count number of lines changed.

        Returns:
            Line count
        """
        result = await self.run_git(["diff", "--shortstat"])
        # Parse output like " 3 files changed, 15 insertions(+), 5 deletions(-)"
        match = re.search(r"(\d+) insertions?", result["output"])
        insertions = int(match.group(1)) if match else 0

        match = re.search(r"(\d+) deletions?", result["output"])
        deletions = int(match.group(1)) if match else 0

        return insertions + deletions

    async def check_todos(self) -> int:
        """Check for new TODO/FIXME comments in diff.

        Returns:
            Number of new TODOs found
        """
        result = await self.run_git(["diff"])
        diff_lines = result["output"].split("\n")

        todo_count = 0
        todo_pattern = re.compile(r"^\+.*TODO|FIXME|HACK|XXX", re.IGNORECASE)

        for line in diff_lines:
            if todo_pattern.match(line):
                # Exclude TODOs that existed before (lines starting with ++)
                if not line.startswith("+++") and not line.startswith("+++"):
                    todo_count += 1

        return todo_count

    async def validate_scope(
        self,
        allowed_paths: list[str],
        denied_paths: list[str],
    ) -> dict:
        """Validate that changes respect allow/deny lists.

        Args:
            allowed_paths: List of allowed path prefixes
            denied_paths: List of denied path prefixes

        Returns:
            Dict with valid (bool) and violations (list[str])
        """
        changed_files = await self.list_files_changed()

        violations = []
        for file_path in changed_files:
            # Check denied paths first
            denied = any(file_path.startswith(deny) for deny in denied_paths)
            if denied:
                violations.append(f"Denied path: {file_path}")
                continue

            # Check if allowed (if allow list not empty)
            if allowed_paths:
                allowed = any(file_path.startswith(allow) for allow in allowed_paths)
                if not allowed:
                    violations.append(f"Not in allowed paths: {file_path}")

        return {
            "valid": len(violations) == 0,
            "violations": violations,
        }

    async def create_worktree(
        self,
        worktree_path: Path,
        branch: str,
        force: bool = False,
    ) -> None:
        """Create a new git worktree.

        Args:
            worktree_path: Path for new worktree
            branch: Branch to checkout in worktree
            force: Force creation even if worktree_path exists

        Raises:
            GitError: If worktree creation fails
        """
        # Ensure repo has an initial commit before creating worktrees
        await self.ensure_initialized()

        # Create parent directory
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing worktree if force or stale directory
        if worktree_path.exists():
            if force:
                logger.warning(f"Removing existing worktree: {worktree_path}")
                await self.remove_worktree(worktree_path)
            else:
                # Stale directory from a prior failed worktree
                logger.warning(f"Removing stale worktree directory: {worktree_path}")
                shutil.rmtree(worktree_path, ignore_errors=True)

        # Create worktree (create branch if needed)
        branch_exists = await self.branch_exists(branch)
        if branch_exists:
            args = ["worktree", "add", str(worktree_path), branch]
        else:
            base_branch = await self.get_current_branch()
            args = ["worktree", "add", "-b", branch, str(worktree_path), base_branch]
        result = await self.run_git(args, check=False)

        if not result["success"]:
            raise GitError(f"Failed to create worktree: {result['output']}")

        logger.info(f"Created worktree: {worktree_path} (branch: {branch})")

    async def branch_exists(self, branch: str) -> bool:
        """Check if a local branch exists."""
        result = await self.run_git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False,
        )
        return result["success"]

    async def ensure_initialized(self) -> None:
        """Ensure repository has an initial commit."""
        head_check = await self.run_git(["rev-parse", "--verify", "HEAD"], check=False)
        if head_check["success"]:
            return

        # Derive initial branch name (fallback to main)
        branch_name = "main"
        head_ref = await self.run_git(["symbolic-ref", "HEAD"], check=False)
        if head_ref["success"]:
            ref = head_ref["output"].strip()
            if ref.startswith("refs/heads/"):
                branch_name = ref.split("/")[-1]

        # Create branch and initial commit
        await self.run_git(["checkout", "-B", branch_name], check=False)
        await self.run_git(["add", "-A"], check=False)
        commit_result = await self.run_git(["commit", "-m", "Initial commit"], check=False)
        if not commit_result["success"]:
            raise GitError(f"Failed to create initial commit: {commit_result['output']}")

    async def remove_worktree(self, worktree_path: Path) -> None:
        """Remove a git worktree.

        Args:
            worktree_path: Path to worktree to remove
        """
        # Try to prune worktree first (cleaner removal)
        await self.run_git(
            ["worktree", "remove", str(worktree_path)],
            check=False,
        )

        logger.info(f"Removed worktree: {worktree_path}")

    async def list_worktrees(self) -> list[dict]:
        """List all git worktrees.

        Returns:
            List of worktree info dicts with 'path' and 'branch' keys
        """
        result = await self.run_git(["worktree", "list", "--porcelain"])

        worktrees = []
        for line in result["output"].split("\n"):
            if not line.strip():
                continue

            if line.startswith("worktree "):
                path = Path(line.split()[1])
                worktrees.append({"path": path})
            elif line.startswith("branch ") and worktrees:
                branch = line.split()[1].strip("[]")
                worktrees[-1]["branch"] = branch

        return worktrees

    async def worktree_exists(self, worktree_path: Path) -> bool:
        """Check if a worktree exists.

        Args:
            worktree_path: Path to check

        Returns:
            True if worktree exists
        """
        worktrees = await self.list_worktrees()
        return any(str(w["path"]) == str(worktree_path) for w in worktrees)

    async def prune_worktrees(self) -> None:
        """Prune working tree files in $GIT_DIR/worktrees."""
        await self.run_git(["worktree", "prune"])
        logger.info("Pruned worktrees")
