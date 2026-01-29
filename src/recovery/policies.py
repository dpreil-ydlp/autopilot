"""Recovery and retry policies."""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..utils.git import GitOps

logger = logging.getLogger(__name__)


class PushErrorType(str, Enum):
    """Types of push errors."""

    AUTH = "auth"
    NON_FAST_FORWARD = "non_fast_forward"
    NETWORK = "network"
    POLICY = "policy"
    UNKNOWN = "unknown"


@dataclass
class PushError:
    """Push error details."""

    error_type: PushErrorType
    message: str
    recoverable: bool


@dataclass
class RetryConfig:
    """Retry configuration."""

    planner_max_retries: int = 1
    builder_max_retries: int = 1
    reviewer_max_retries: int = 1
    push_max_retries: int = 2
    validation_max_retries: int = 0  # Feed into FIX loop instead


class RetryPolicy:
    """Manage retry policies for different operations."""

    def __init__(self, config: RetryConfig | None = None):
        """Initialize retry policy.

        Args:
            config: Retry configuration
        """
        self.config = config or RetryConfig()

    def should_retry_planner(self, attempt: int) -> bool:
        """Check if planner should be retried.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            True if should retry
        """
        return attempt < self.config.planner_max_retries

    def should_retry_builder(self, attempt: int) -> bool:
        """Check if builder should be retried.

        Args:
            attempt: Current attempt number

        Returns:
            True if should retry
        """
        return attempt < self.config.builder_max_retries

    def should_retry_reviewer(self, attempt: int) -> bool:
        """Check if reviewer should be retried.

        Args:
            attempt: Current attempt number

        Returns:
            True if should retry
        """
        return attempt < self.config.reviewer_max_retries

    def should_retry_push(self, attempt: int) -> bool:
        """Check if push should be retried.

        Args:
            attempt: Current attempt number

        Returns:
            True if should retry
        """
        return attempt < self.config.push_max_retries

    def should_retry_validation(self, attempt: int) -> bool:
        """Check if validation should be retried.

        Validation failures go into FIX loop, not retries.

        Args:
            attempt: Current attempt number

        Returns:
            False (always go to FIX loop)
        """
        return False  # Validation failures feed FIX loop


class PushRecovery:
    """Recover from push failures."""

    def __init__(self, git_ops: GitOps):
        """Initialize push recovery.

        Args:
            git_ops: Git operations wrapper
        """
        self.git_ops = git_ops

    def classify_error(self, stderr: str) -> PushError:
        """Classify push error type.

        Args:
            stderr: Error output from git push

        Returns:
            PushError with classification
        """
        stderr_lower = stderr.lower()

        if "auth" in stderr_lower or "credential" in stderr_lower:
            return PushError(
                error_type=PushErrorType.AUTH,
                message="Authentication failed",
                recoverable=False,
            )
        elif "non-fast-forward" in stderr_lower:
            return PushError(
                error_type=PushErrorType.NON_FAST_FORWARD,
                message="Branch is not up to date",
                recoverable=True,
            )
        elif "network" in stderr_lower or "connection" in stderr_lower:
            return PushError(
                error_type=PushErrorType.NETWORK,
                message="Network error",
                recoverable=True,
            )
        elif "policy" in stderr_lower or "protected" in stderr_lower:
            return PushError(
                error_type=PushErrorType.POLICY,
                message="Repository policy rejected push",
                recoverable=False,
            )
        else:
            return PushError(
                error_type=PushErrorType.UNKNOWN,
                message=f"Unknown error: {stderr[:200]}",
                recoverable=False,
            )

    async def recover(
        self,
        error: PushError,
        remote: str,
        branch: str,
    ) -> bool:
        """Attempt to recover from push error.

        Args:
            error: Classified push error
            remote: Remote name
            branch: Branch name

        Returns:
            True if recovery successful

        Raises:
            Exception: If recovery fails
        """
        if error.error_type == PushErrorType.NON_FAST_FORWARD:
            logger.info("Recovering from non-fast-forward: fetch and rebase")
            await self.git_ops.run_git(["fetch", remote])
            await self.git_ops.run_git(["rebase", f"{remote}/{branch}"])
            return True

        elif error.error_type == PushErrorType.NETWORK:
            logger.info("Network error - caller should retry with backoff")
            return True  # Retry will be handled by caller

        elif error.error_type == PushErrorType.AUTH:
            # Try to get auth status
            logger.error("Authentication error - checking git credentials")
            try:
                # Check if gh CLI is available for GitHub
                result = await self.git_ops.run_git(
                    ["config", "--get", "credential.helper"],
                    check=False,
                )
                logger.info(f"Credential helper: {result['output']}")
            except Exception:
                pass
            return False  # Not recoverable

        elif error.error_type == PushErrorType.POLICY:
            logger.error("Policy error - not recoverable without human intervention")
            return False

        else:
            logger.error(f"Unknown error type: {error.error_type}")
            return False

    async def create_fallback_artifact(
        self,
        branch: str,
        output_dir: Path,
    ) -> Path:
        """Create fallback artifact for failed push.

        Args:
            branch: Branch name
            output_dir: Directory for artifacts

        Returns:
            Path to created artifact
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create patch
        patch_path = output_dir / f"{branch.replace('/', '_')}_fallback.patch"
        await self.git_ops.create_patch(patch_path)

        # Create bundle
        bundle_path = output_dir / f"{branch.replace('/', '_')}_fallback.bundle"
        await self.git_ops.create_bundle(bundle_path, branch)

        logger.info(f"Created fallback artifacts: {patch_path}, {bundle_path}")

        return patch_path


class RecoveryManager:
    """Manage recovery from various failure types."""

    def __init__(
        self,
        git_ops: GitOps,
        retry_config: RetryConfig | None = None,
    ):
        """Initialize recovery manager.

        Args:
            git_ops: Git operations wrapper
            retry_config: Retry configuration
        """
        self.git_ops = git_ops
        self.retry_policy = RetryPolicy(retry_config)
        self.push_recovery = PushRecovery(git_ops)

    async def retry_operation(
        self,
        operation: str,
        attempt: int,
        func,
        *args,
        **kwargs,
    ):
        """Retry operation with policy.

        Args:
            operation: Operation type (planner, builder, reviewer, push)
            attempt: Current attempt number
            func: Async function to execute
            *args: Function args
            **kwargs: Function kwargs

        Returns:
            Function result

        Raises:
            Exception: If retries exhausted
        """
        should_retry = False

        if operation == "planner":
            should_retry = self.retry_policy.should_retry_planner(attempt)
        elif operation == "builder":
            should_retry = self.retry_policy.should_retry_builder(attempt)
        elif operation == "reviewer":
            should_retry = self.retry_policy.should_retry_reviewer(attempt)
        elif operation == "push":
            should_retry = self.retry_policy.should_retry_push(attempt)
        elif operation == "validation":
            should_retry = self.retry_policy.should_retry_validation(attempt)
        else:
            logger.warning(f"Unknown operation type: {operation}")
            should_retry = False

        if not should_retry and attempt > 0:
            logger.error(f"Retries exhausted for {operation} (attempt {attempt})")
            raise Exception(f"{operation} failed after {attempt} retries")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"{operation} attempt {attempt + 1} failed: {e}")
            raise
