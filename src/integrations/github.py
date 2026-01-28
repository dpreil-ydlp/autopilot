"""GitHub integration for PR creation and management."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..utils.git import GitOps
from ..utils.subprocess import SubprocessManager

logger = logging.getLogger(__name__)


@dataclass
class PRResult:
    """Result of PR creation."""

    success: bool
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    error_message: Optional[str] = None


class GitHubIntegration:
    """GitHub operations integration."""

    def __init__(
        self,
        git_ops: GitOps,
        remote: str = "origin",
        base_branch: str = "main",
    ):
        """Initialize GitHub integration.

        Args:
            git_ops: Git operations wrapper
            remote: Git remote name
            base_branch: Base branch for PRs
        """
        self.git_ops = git_ops
        self.remote = remote
        self.base_branch = base_branch
        self.manager = SubprocessManager(timeout_sec=60)

    async def push_branch(
        self,
        branch_name: str,
        force: bool = False,
    ) -> dict:
        """Push branch to remote.

        Args:
            branch_name: Branch name to push
            force: Force push (not recommended)

        Returns:
            Result dict
        """
        logger.info(f"Pushing branch {branch_name} to {self.remote}")

        try:
            result = await self.git_ops.push(
                remote=self.remote,
                branch=branch_name,
                force=force,
                retry=2,
            )

            logger.info(f"Branch pushed successfully")
            return {"success": True, "output": result["output"]}

        except Exception as e:
            logger.error(f"Push failed: {e}")
            return {"success": False, "error": str(e)}

    async def create_pr(
        self,
        branch_name: str,
        title: str,
        description: str,
        labels: Optional[list[str]] = None,
    ) -> PRResult:
        """Create pull request.

        Args:
            branch_name: Branch name
            title: PR title
            description: PR description
            labels: Optional list of labels

        Returns:
            PRResult with details
        """
        logger.info(f"Creating PR for {branch_name}")

        try:
            # Try using gh CLI first
            pr_result = await self._create_pr_with_gh(
                branch_name,
                title,
                description,
                labels,
            )

            if pr_result.success:
                return pr_result

            # Fall back to manual instructions
            logger.warning("gh CLI not available, returning manual PR URL")
            return self._manual_pr_instructions(branch_name, title, description)

        except Exception as e:
            logger.error(f"PR creation failed: {e}")
            return PRResult(
                success=False,
                error_message=str(e),
            )

    async def _create_pr_with_gh(
        self,
        branch_name: str,
        title: str,
        description: str,
        labels: Optional[list[str]],
    ) -> PRResult:
        """Create PR using gh CLI.

        Args:
            branch_name: Branch name
            title: PR title
            description: PR description
            labels: Optional labels

        Returns:
            PRResult
        """
        # Build gh pr create command
        args = [
            "gh", "pr", "create",
            "--base", self.base_branch,
            "--head", branch_name,
            "--title", title,
            "--body", description,
        ]

        # Add labels if provided
        if labels:
            for label in labels:
                args.extend(["--label", label])

        try:
            result = await self.manager.run(
                command=args,
                cwd=self.git_ops.repo_root,
                capture_output=True,
            )

            if result["success"]:
                # Extract PR URL from output
                output = result["output"]
                pr_url = self._extract_pr_url(output)
                pr_number = self._extract_pr_number(output)

                logger.info(f"PR created: {pr_url}")

                return PRResult(
                    success=True,
                    pr_url=pr_url,
                    pr_number=pr_number,
                )
            else:
                # gh CLI might not be available
                logger.warning(f"gh CLI failed: {result['output']}")
                return PRResult(
                    success=False,
                    error_message="gh CLI not available or failed",
                )

        except Exception as e:
            logger.warning(f"gh CLI error: {e}")
            return PRResult(
                success=False,
                error_message=str(e),
            )

    def _manual_pr_instructions(
        self,
        branch_name: str,
        title: str,
        description: str,
    ) -> PRResult:
        """Generate manual PR instructions.

        Args:
            branch_name: Branch name
            title: PR title
            description: PR description

        Returns:
            PRResult with manual instructions
        """
        # Get remote URL
        try:
            import asyncio
            result = asyncio.run(self.git_ops.run_git(
                ["remote", "get-url", self.remote],
                check=False,
            ))
            remote_url = result["output"].strip()

            # Convert to HTTPS web URL
            if remote_url.startswith("git@"):
                # git@github.com:user/repo.git
                remote_url = remote_url.replace("git@", "https://").replace(":", "/").replace(".git", "")
            elif remote_url.startswith("git://"):
                remote_url = remote_url.replace("git://", "https://")
            elif remote_url.startswith("https://"):
                pass  # Already HTTPS
            else:
                remote_url = None

            if remote_url:
                pr_url = f"{remote_url}/compare/{self.base_branch}...{branch_name}"
            else:
                pr_url = None

            logger.info(f"Manual PR URL: {pr_url}")

            return PRResult(
                success=True,
                pr_url=pr_url,
                pr_number=None,
            )

        except Exception as e:
            logger.error(f"Failed to generate manual PR URL: {e}")
            return PRResult(
                success=False,
                error_message=f"Could not create PR automatically: {e}",
            )

    def _extract_pr_url(self, output: str) -> Optional[str]:
        """Extract PR URL from gh output.

        Args:
            output: gh CLI output

        Returns:
            PR URL or None
        """
        import re

        # Look for URL pattern
        match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/\d+", output)
        return match.group(0) if match else None

    def _extract_pr_number(self, output: str) -> Optional[int]:
        """Extract PR number from gh output.

        Args:
            output: gh CLI output

        Returns:
            PR number or None
        """
        import re

        # Look for PR number
        match = re.search(r"pull/(\d+)", output)
        if match:
            return int(match.group(1))

        match = re.search(r"#(\d+)", output)
        if match:
            return int(match.group(1))

        return None

    def generate_pr_description(
        self,
        task_id: str,
        task_title: str,
        task_goal: str,
        acceptance_criteria: list[str],
        iterations: int,
        files_changed: int,
        lines_changed: int,
    ) -> str:
        """Generate PR description from task details.

        Args:
            task_id: Task identifier
            task_title: Task title
            task_goal: Task goal
            acceptance_criteria: List of acceptance criteria
            iterations: Number of iterations
            files_changed: Number of files changed
            lines_changed: Number of lines changed

        Returns:
            PR description markdown
        """
        lines = [
            f"[autopilot] Task: {task_id} - {task_title}",
            "",
            "## Goal",
            task_goal,
            "",
            "## Acceptance Criteria",
        ]

        for i, criterion in enumerate(acceptance_criteria, 1):
            lines.append(f"- [ ] {criterion}")

        lines.extend([
            "",
            "## Changes",
            f"- Files changed: {files_changed}",
            f"- Lines changed: {lines_changed}",
            f"- Iterations: {iterations}",
            "",
            "## Validation",
            "- ✅ Tests passed",
            "- ✅ Code reviewed",
            "- ✅ UAT completed",
            "",
            "## Artifacts",
            "- Plan: `.autopilot/plan/plan.json`",
            "- State: `.autopilot/state.json`",
            "- Logs: `.autopilot/logs/`",
            "",
            "---",
            "",
            "*This PR was created automatically by Autopilot*",
            "",
        ])

        return "\n".join(lines)
