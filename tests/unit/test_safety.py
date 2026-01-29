"""Unit tests for safety guards."""

from pathlib import Path

import pytest

from src.recovery.policies import (
    PushErrorType,
    PushRecovery,
    RetryConfig,
    RetryPolicy,
)
from src.safety.guards import (
    KillSwitch,
    ScopeCheckResult,
)


@pytest.fixture
def temp_kill_switch_files(tmp_path):
    """Create temporary kill switch files."""
    stop_file = tmp_path / "AUTOPILOT_STOP"
    pause_file = tmp_path / "AUTOPILOT_PAUSE"
    skip_file = tmp_path / "AUTOPILOT_SKIP_REVIEW"

    return {
        "stop": stop_file,
        "pause": pause_file,
        "skip": skip_file,
    }


def test_kill_switch_no_files(tmp_path):
    """Test KillSwitch when no files exist."""
    # Change to temp directory
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)

    try:
        assert not KillSwitch.check_stop()
        assert not KillSwitch.check_pause()
        assert not KillSwitch.check_skip_review()
    finally:
        os.chdir(original_cwd)


def test_kill_switch_stop_file(tmp_path):
    """Test KillSwitch detects stop file."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)

    # Create .autopilot directory first
    Path(".autopilot").mkdir(exist_ok=True)

    try:
        KillSwitch.STOP_FILE.touch()
        assert KillSwitch.check_stop()

        KillSwitch.clear_stop()
        assert not KillSwitch.check_stop()
    finally:
        os.chdir(original_cwd)


def test_kill_switch_pause_file(tmp_path):
    """Test KillSwitch detects pause file."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)

    # Create .autopilot directory first
    Path(".autopilot").mkdir(exist_ok=True)

    try:
        KillSwitch.PAUSE_FILE.touch()
        assert KillSwitch.check_pause()

        KillSwitch.clear_pause()
        assert not KillSwitch.check_pause()
    finally:
        os.chdir(original_cwd)


def test_kill_switch_skip_review_file(tmp_path):
    """Test KillSwitch detects skip review file."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)

    # Create .autopilot directory first
    Path(".autopilot").mkdir(exist_ok=True)

    try:
        KillSwitch.SKIP_REVIEW_FILE.touch()
        assert KillSwitch.check_skip_review()

        KillSwitch.clear_skip_review()
        assert not KillSwitch.check_skip_review()
    finally:
        os.chdir(original_cwd)


def test_scope_check_result():
    """Test ScopeCheckResult creation."""
    result = ScopeCheckResult(
        valid=False,
        violations=["Violation 1", "Violation 2"],
        diff_lines=500,
        todo_count=2,
    )

    assert not result.valid
    assert len(result.violations) == 2
    assert result.diff_lines == 500
    assert result.todo_count == 2


def test_retry_policy_defaults():
    """Test RetryPolicy with default config."""
    policy = RetryPolicy()

    assert policy.should_retry_planner(0)
    assert not policy.should_retry_planner(1)  # Max retries = 1

    assert policy.should_retry_builder(0)
    assert not policy.should_retry_builder(1)

    assert policy.should_retry_push(0)
    assert policy.should_retry_push(1)  # Max retries = 2
    assert not policy.should_retry_push(2)

    assert not policy.should_retry_validation(0)  # Always go to FIX loop


def test_retry_policy_custom_config():
    """Test RetryPolicy with custom config."""
    config = RetryConfig(
        planner_max_retries=3,
        builder_max_retries=3,
        push_max_retries=5,
    )
    policy = RetryPolicy(config)

    assert policy.should_retry_planner(0)
    assert policy.should_retry_planner(1)
    assert policy.should_retry_planner(2)
    assert not policy.should_retry_planner(3)

    assert policy.should_retry_push(0)
    assert policy.should_retry_push(4)
    assert not policy.should_retry_push(5)


def test_push_recovery_classify_auth():
    """Test push error classification for auth errors."""
    recovery = PushRecovery(None)

    error = recovery.classify_error("fatal: authentication failed")
    assert error.error_type == PushErrorType.AUTH
    assert not error.recoverable


def test_push_recovery_classify_non_fast_forward():
    """Test push error classification for non-fast-forward."""
    recovery = PushRecovery(None)

    error = recovery.classify_error("non-fast-forward")
    assert error.error_type == PushErrorType.NON_FAST_FORWARD
    assert error.recoverable


def test_push_recovery_classify_network():
    """Test push error classification for network errors."""
    recovery = PushRecovery(None)

    error = recovery.classify_error("could not connect to network")
    assert error.error_type == PushErrorType.NETWORK
    assert error.recoverable


def test_push_recovery_classify_policy():
    """Test push error classification for policy errors."""
    recovery = PushRecovery(None)

    error = recovery.classify_error("rejected by policy")
    assert error.error_type == PushErrorType.POLICY
    assert not error.recoverable


def test_push_recovery_classify_unknown():
    """Test push error classification for unknown errors."""
    recovery = PushRecovery(None)

    error = recovery.classify_error("some unknown error")
    assert error.error_type == PushErrorType.UNKNOWN
    assert not error.recoverable
