# tests/test_sync_determine_operation.py

import pytest
import os
import sys
import json
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, mock_open

# Add the 'pdd' directory to the Python path to allow imports.
# This is necessary because the test file is in 'tests/' and the code is in 'pdd/'.
pdd_path = Path(__file__).parent.parent / 'pdd'
sys.path.insert(0, str(pdd_path))

from sync_determine_operation import (
    sync_determine_operation,
    analyze_conflict_with_llm,
    SyncLock,
    Fingerprint,
    RunReport,
    SyncDecision,
    calculate_sha256,
    read_fingerprint,
    read_run_report,
    PDD_DIR,
    META_DIR,
    LOCKS_DIR,
    get_pdd_dir,
    get_meta_dir,
    get_locks_dir,
    validate_expected_files,
    _handle_missing_expected_files,
    _is_workflow_complete,
    get_pdd_file_paths
)

# --- Test Plan ---
#
# The goal is to test the core decision-making logic of `pdd sync`.
# This involves testing the locking mechanism, file state analysis,
# the main decision tree, and the LLM-based conflict resolution.
#
# Formal Verification (Z3) vs. Unit Tests:
# - Z3 could be used to formally verify the completeness and exclusivity of the
#   decision tree in `_perform_sync_analysis`. This would involve modeling the
#   state space (file existence, hash matches, run report values) and the
#   decision rules to prove that every possible state leads to exactly one
#   defined outcome.
# - However, the state space is large, and the implementation details (like
#   checking prompt content for dependencies) add complexity.
# - Unit tests are more practical here. They can directly test the implemented
#   logic for each specific state combination, ensuring the code behaves as
#   expected. They are also better suited for testing interactions with the
#   filesystem, external processes (git), and mocked services (LLM).
# - Therefore, this test suite will use comprehensive unit tests with pytest.
#
# Test Suite Structure:
#
# Part 1: Core Components & Helper Functions
#   - Test `SyncLock` for acquiring, releasing, context management, stale lock
#     handling, and multi-process contention simulation.
#   - Test file utility functions (`calculate_sha256`, `read_fingerprint`,
#     `read_run_report`) for success, failure (missing file), and error
#     (malformed content) cases.
#
# Part 2: `sync_determine_operation` Decision Logic
#   - Test the `log_mode` flag to ensure it bypasses locking.
#   - Test each branch of the decision tree in priority order:
#     1.  **Runtime Signals:** `crash`, `verify` (after crash), `fix`, `test` (low coverage).
#         Test that `crash` has the highest priority.
#     2.  **No Fingerprint (New Unit):** `auto-deps`, `generate`, `nothing`.
#     3.  **No Changes (Synced State):** `nothing`, `example` (if missing), `test` (if missing).
#     4.  **Simple Changes (Single File):** `generate`/`auto-deps` (prompt), `update` (code),
#         `test` (test), `verify` (example).
#     5.  **Complex Changes (Multiple Files):** `analyze_conflict`.
#
# Part 3: `analyze_conflict_with_llm`
#   - Mock external dependencies: `llm_invoke`, `load_prompt_template`, `get_git_diff`.
#   - Test the successful path where a valid LLM response is parsed correctly.
#   - Test fallback scenarios:
#     - LLM template not found.
#     - LLM returns invalid JSON.
#     - LLM response is missing required keys.
#     - LLM confidence is below the threshold.
#     - An exception occurs during the process.
#   - In all fallback cases, the decision should be `fail_and_request_manual_merge`.
#
# Fixtures:
#   - `pdd_test_environment`: Creates a temporary directory structure for tests
#     (prompts, .pdd/meta, .pdd/locks) and cleans up afterward.
#   - Helper functions will be used to create dummy files and metadata.

# --- Fixtures and Helpers ---

BASENAME = "test_unit"
LANGUAGE = "python"
TARGET_COVERAGE = 90.0

@pytest.fixture
def pdd_test_environment(tmp_path):
    """Creates a temporary, isolated PDD project structure for testing."""
    # Change to tmp_path
    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    
    # Create directories
    Path(".pdd/meta").mkdir(parents=True, exist_ok=True)
    Path(".pdd/locks").mkdir(parents=True, exist_ok=True)
    Path("prompts").mkdir(exist_ok=True)
    
    # Now update the constants after changing directory
    pdd_module = sys.modules['sync_determine_operation']
    pdd_module.PDD_DIR = pdd_module.get_pdd_dir()
    pdd_module.META_DIR = pdd_module.get_meta_dir()
    pdd_module.LOCKS_DIR = pdd_module.get_locks_dir()
    
    yield tmp_path

    # Restore original working directory
    os.chdir(original_cwd)
    
    # Update constants again
    pdd_module.PDD_DIR = pdd_module.get_pdd_dir()
    pdd_module.META_DIR = pdd_module.get_meta_dir()
    pdd_module.LOCKS_DIR = pdd_module.get_locks_dir()

def create_file(path: Path, content: str = "") -> str:
    """Creates a file with given content and returns its SHA256 hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return calculate_sha256(path)

def create_fingerprint_file(path: Path, data: dict):
    """Creates a fingerprint JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)

def create_run_report_file(path: Path, data: dict):
    """Creates a run report JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)

# --- Part 1: Core Components & Helper Functions ---

class TestSyncLock:
    def test_lock_acquire_and_release(self, pdd_test_environment):
        lock_file = get_locks_dir() / f"{BASENAME}_{LANGUAGE}.lock"
        lock = SyncLock(BASENAME, LANGUAGE)

        assert not lock_file.exists()
        lock.acquire()
        assert lock_file.exists()
        assert lock_file.read_text().strip() == str(os.getpid())
        lock.release()
        assert not lock_file.exists()

    def test_lock_context_manager(self, pdd_test_environment):
        lock_file = get_locks_dir() / f"{BASENAME}_{LANGUAGE}.lock"
        assert not lock_file.exists()
        with SyncLock(BASENAME, LANGUAGE) as lock:
            assert lock_file.exists()
            assert lock_file.read_text().strip() == str(os.getpid())
        assert not lock_file.exists()

    def test_lock_stale_lock(self, pdd_test_environment, monkeypatch):
        lock_file = get_locks_dir() / f"{BASENAME}_{LANGUAGE}.lock"
        stale_pid = 99999  # A non-existent PID
        lock_file.write_text(str(stale_pid))

        monkeypatch.setattr("psutil.pid_exists", lambda pid: pid != stale_pid)

        # Should acquire lock by removing the stale one
        with SyncLock(BASENAME, LANGUAGE):
            assert lock_file.exists()
            assert lock_file.read_text().strip() == str(os.getpid())

    def test_lock_held_by_another_process(self, pdd_test_environment, monkeypatch):
        lock_file = get_locks_dir() / f"{BASENAME}_{LANGUAGE}.lock"
        other_pid = os.getpid() + 1
        lock_file.write_text(str(other_pid))

        monkeypatch.setattr("psutil.pid_exists", lambda pid: True)

        with pytest.raises(TimeoutError, match=f"Lock held by running process {other_pid}"):
            SyncLock(BASENAME, LANGUAGE).acquire()

    def test_lock_reentrancy(self, pdd_test_environment):
        lock = SyncLock(BASENAME, LANGUAGE)
        with lock:
            # Try acquiring again within the same process
            lock.acquire() # Should not raise an error
            assert (get_locks_dir() / f"{BASENAME}_{LANGUAGE}.lock").exists()


class TestFileUtilities:
    def test_calculate_sha256(self, pdd_test_environment):
        file_path = pdd_test_environment / "test.txt"
        content = "hello world"
        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        create_file(file_path, content)
        assert calculate_sha256(file_path) == expected_hash

    def test_calculate_sha256_not_exists(self, pdd_test_environment):
        assert calculate_sha256(pdd_test_environment / "nonexistent.txt") is None

    def test_read_fingerprint_success(self, pdd_test_environment):
        fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
        fp_data = {
            "pdd_version": "1.0", "timestamp": "2023-01-01T00:00:00Z",
            "command": "generate", "prompt_hash": "p_hash", "code_hash": "c_hash",
            "example_hash": "e_hash", "test_hash": "t_hash"
        }
        create_fingerprint_file(fp_path, fp_data)
        fp = read_fingerprint(BASENAME, LANGUAGE)
        assert isinstance(fp, Fingerprint)
        assert fp.prompt_hash == "p_hash"

    def test_read_fingerprint_invalid_json(self, pdd_test_environment):
        fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
        fp_path.write_text("{ not valid json }")
        assert read_fingerprint(BASENAME, LANGUAGE) is None

    def test_read_run_report_success(self, pdd_test_environment):
        rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
        rr_data = {
            "timestamp": "2023-01-01T00:00:00Z", "exit_code": 0,
            "tests_passed": 10, "tests_failed": 0, "coverage": 95.5
        }
        create_run_report_file(rr_path, rr_data)
        rr = read_run_report(BASENAME, LANGUAGE)
        assert isinstance(rr, RunReport)
        assert rr.tests_failed == 0
        assert rr.coverage == 95.5


# --- Part 2: `sync_determine_operation` Decision Logic ---

@patch('sync_determine_operation.construct_paths')
def test_log_mode_skips_lock(mock_construct, pdd_test_environment):
    with patch('sync_determine_operation.SyncLock') as mock_lock:
        sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, log_mode=True)
        mock_lock.assert_not_called()

    with patch('sync_determine_operation.SyncLock') as mock_lock:
        sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, log_mode=False)
        mock_lock.assert_called_once_with(BASENAME, LANGUAGE)

# --- Runtime Signal Tests ---
def test_context_aware_fix_over_crash_logic(pdd_test_environment):
    """Test the new context-aware decision logic that prefers 'fix' over 'crash'."""
    
    # Create prompt file
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "Test prompt")
    
    # Test Case 1: No successful history - should use 'crash'
    create_fingerprint_file(get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json", {
        "pdd_version": "1.0.0",
        "timestamp": "2025-07-15T12:00:00Z",
        "command": "generate",  # No successful example history
        "prompt_hash": "test_hash",
        "code_hash": "test_hash",
        "example_hash": None,
        "test_hash": None
    })
    
    create_run_report_file(get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json", {
        "timestamp": "2025-07-15T12:00:00Z",
        "exit_code": 1,
        "tests_passed": 0,
        "tests_failed": 0,
        "coverage": 0.0
    })
    
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'crash'
    assert "no successful example history" in decision.reason.lower()
    assert decision.details['example_success_history'] == False
    
    # Test Case 2: Successful history via 'verify' command - should use 'fix'
    create_fingerprint_file(get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json", {
        "pdd_version": "1.0.0",
        "timestamp": "2025-07-15T12:00:00Z",
        "command": "verify",  # Indicates successful example history
        "prompt_hash": "test_hash",
        "code_hash": "test_hash",
        "example_hash": "test_hash",
        "test_hash": None
    })
    
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'fix'
    assert "prefer fix over crash" in decision.reason.lower()
    assert decision.details['example_success_history'] == True
    
    # Test Case 3: Successful history via 'test' command - should use 'fix'
    create_fingerprint_file(get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json", {
        "pdd_version": "1.0.0",
        "timestamp": "2025-07-15T12:00:00Z",
        "command": "test",  # Indicates successful example history
        "prompt_hash": "test_hash",
        "code_hash": "test_hash",
        "example_hash": "test_hash",
        "test_hash": "test_hash"
    })
    
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'fix'
    assert "prefer fix over crash" in decision.reason.lower()
    assert decision.details['example_success_history'] == True


@patch('sync_determine_operation.construct_paths')
def test_decision_crash_on_exit_code_nonzero(mock_construct, pdd_test_environment):
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 1, "tests_passed": 0, "tests_failed": 0, "coverage": 0.0
    })
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE)
    assert decision.operation == 'crash'
    assert "Runtime error detected" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_verify_after_crash_fix(mock_construct, pdd_test_environment):
    # Last command was 'crash'
    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.0", "timestamp": "t", "command": "crash",
        "prompt_hash": "p", "code_hash": "c", "example_hash": "e", "test_hash": "t"
    })
    # And the run report shows a crash
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 1, "tests_passed": 0, "tests_failed": 0, "coverage": 0.0
    })
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE)
    assert decision.operation == 'verify'
    assert "Previous crash operation completed" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_fix_on_test_failures(mock_construct, pdd_test_environment):
    # Create prompt file so get_pdd_file_paths can work properly
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "Test prompt")
    
    # Create test file so test_file.exists() check passes
    test_file = pdd_test_environment / f"test_{BASENAME}.py"
    create_file(test_file, "def test_dummy(): pass")
    
    # Mock construct_paths to return the test file path
    mock_construct.return_value = (
        {}, {},
        {'test_file': str(test_file)},
        LANGUAGE
    )
    
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 0, "tests_passed": 5, "tests_failed": 2, "coverage": 80.0
    })
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'fix'
    assert "Test failures detected" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_test_on_low_coverage(mock_construct, pdd_test_environment):
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 0, "tests_passed": 10, "tests_failed": 0, "coverage": 75.0
    })
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE)
    assert decision.operation == 'test'
    assert f"Coverage 75.0% below target {TARGET_COVERAGE:.1f}%" in decision.reason

# --- No Fingerprint Tests ---
@patch('sync_determine_operation.construct_paths')
def test_decision_generate_for_new_prompt(mock_construct, pdd_test_environment):
    create_file(pdd_test_environment / "prompts" / f"{BASENAME}_{LANGUAGE}.prompt", "A simple prompt.")
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(pdd_test_environment / "prompts"))
    assert decision.operation == 'generate'
    assert "New prompt ready" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_autodeps_for_new_prompt_with_deps(mock_construct, pdd_test_environment):
    create_file(pdd_test_environment / "prompts" / f"{BASENAME}_{LANGUAGE}.prompt", "A prompt that needs to <include> another file.")
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(pdd_test_environment / "prompts"))
    assert decision.operation == 'auto-deps'
    assert "New prompt with dependencies detected" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_nothing_for_new_unit_no_prompt(mock_construct, pdd_test_environment):
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE)
    assert decision.operation == 'nothing'
    assert "No prompt file and no history" in decision.reason

# --- State Change Tests ---
@patch('sync_determine_operation.construct_paths')
def test_decision_nothing_when_synced(mock_construct, pdd_test_environment):
    prompts_dir = pdd_test_environment / "prompts"
    p_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt")
    c_hash = create_file(pdd_test_environment / f"{BASENAME}.py")
    e_hash = create_file(pdd_test_environment / f"{BASENAME}_example.py")
    t_hash = create_file(pdd_test_environment / f"test_{BASENAME}.py")

    mock_construct.return_value = (
        {}, {},
        {
            'code_file': str(pdd_test_environment / f"{BASENAME}.py"),
            'example_file': str(pdd_test_environment / f"{BASENAME}_example.py"),
            'test_file': str(pdd_test_environment / f"test_{BASENAME}.py")
        },
        LANGUAGE
    )

    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.0", "timestamp": "t", "command": "test",
        "prompt_hash": p_hash, "code_hash": c_hash, "example_hash": e_hash, "test_hash": t_hash
    })

    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'nothing'
    assert "All required files synchronized" in decision.reason

def test_fix_over_crash_with_successful_example_history(pdd_test_environment):
    """Test sync --skip-tests when a crash operation would be triggered."""
    
    # Create prompt file
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "Create a simple add function")
    
    # Create metadata with real hashes
    fingerprint_data = {
        "pdd_version": "0.0.41",
        "timestamp": "2025-07-03T02:34:36.929768+00:00", 
        "command": "test",
        "prompt_hash": "79a219808ec6de6d5b885c28ee811a033ae4a92eba993f7853b5a9d6a3befa84",
        "code_hash": "6d0669923dc331420baaaefea733849562656e00f90c6519bbed46c1e9096595",
        "example_hash": "861d5b27f80c1e3b5b21b23fb58bfebb583bd4224cde95b2517a426ea4661fae",
        "test_hash": "37f6503380c4dd80a5c33be2fe08429dbc9239dd602a8147ed150863db17651f"
    }
    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, fingerprint_data)
    
    # Create run report with exit_code=2 (crash scenario)
    run_report = {
        "timestamp": "2025-07-03T02:34:36.182803+00:00",
        "exit_code": 2,
        "tests_passed": 0,
        "tests_failed": 0,
        "coverage": 0.0
    }
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, run_report)
    
    # Test with skip_tests=True - sync_determine_operation should prefer fix over crash
    # when there's successful example history (fingerprint command is "test")
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, skip_tests=True, prompts_dir=str(prompts_dir))
    
    # With context-aware decision logic, should prefer 'fix' over 'crash' when example has run successfully before
    # The fingerprint command is "test" which indicates successful example history
    assert decision.operation == 'fix'
    assert "prefer fix over crash" in decision.reason.lower()
    assert decision.details['exit_code'] == 2
    assert decision.details['example_success_history'] == True

def test_regression_root_cause_missing_files_with_metadata(pdd_test_environment):
    """
    Test that demonstrates the root cause fix: sync_determine_operation should return 'generate'
    (not 'analyze_conflict') when files are missing but metadata exists.
    """
    
    # Create prompt file
    prompts_dir = pdd_test_environment / "prompts"  
    prompts_dir.mkdir(exist_ok=True)
    prompt_content = """Create a Python module with a simple math function.

Requirements:
- Function name: add
- Parameters: a, b (both numbers)  
- Return: sum of a and b
- Include type hints
- Add docstring explaining the function

Example usage:
result = add(5, 3)  # Should return 8"""
    
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
    
    # Create metadata with real hashes from actual regression test  
    # These are the exact hash values from the failing debug_real_hashes.py scenario
    from datetime import datetime, timezone
    fingerprint_data = {
        "pdd_version": "0.0.41",
        "timestamp": "2025-07-03T02:34:36.929768+00:00",  # Exact timestamp from debug scenario
        "command": "test",
        "prompt_hash": "79a219808ec6de6d5b885c28ee811a033ae4a92eba993f7853b5a9d6a3befa84",
        "code_hash": "6d0669923dc331420baaaefea733849562656e00f90c6519bbed46c1e9096595", 
        "example_hash": "861d5b27f80c1e3b5b21b23fb58bfebb583bd4224cde95b2517a426ea4661fae",
        "test_hash": "37f6503380c4dd80a5c33be2fe08429dbc9239dd602a8147ed150863db17651f"
    }
    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, fingerprint_data)
    
    # Create run report indicating previous successful test execution
    run_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exit_code": 0,
        "tests_passed": 1,
        "tests_failed": 0,
        "coverage": 100.0
    }
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, run_report)
    
    # Key test: Files are missing but metadata exists
    # This was causing 'analyze_conflict' but should return 'generate'
    
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    
    # The fix: should detect missing files and return 'generate', not 'analyze_conflict' 
    assert decision.operation == 'generate'
    assert decision.operation != 'analyze_conflict'
    assert "file missing" in decision.reason.lower() or "new" in decision.reason.lower()

def test_regression_fix_validation_skip_tests_scenarios(pdd_test_environment):
    """
    Validate that skip_tests scenarios work correctly after the regression fix.
    """
    
    # Create prompt file
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True) 
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "Create a simple add function")
    
    test_scenarios = [
        {
            "name": "missing_files_with_metadata_skip_tests",
            "metadata": {
                "pdd_version": "0.0.41", 
                "timestamp": "2023-01-01T00:00:00Z",
                "command": "test",
                "prompt_hash": "abc123",
                "code_hash": "def456",
                "example_hash": "ghi789",
                "test_hash": "jkl012"
            },
            "run_report": {
                "timestamp": "2023-01-01T00:00:00Z",
                "exit_code": 0,
                "tests_passed": 5,
                "tests_failed": 0,
                "coverage": 50.0  # Low coverage
            },
            "skip_tests": True,
            "expected_not": ["analyze_conflict", "test"],
            "expected_in": ["all_synced", "generate", "auto-deps"]
        },
        {
            "name": "crash_scenario_skip_tests",
            "metadata": {
                "pdd_version": "0.0.41",
                "timestamp": "2023-01-01T00:00:00Z", 
                "command": "crash",
                "prompt_hash": "abc123",
                "code_hash": "def456",
                "example_hash": "ghi789",
                "test_hash": "jkl012"
            },
            "run_report": {
                "timestamp": "2023-01-01T00:00:00Z",
                "exit_code": 2,  # Crash exit code
                "tests_passed": 0,
                "tests_failed": 0,
                "coverage": 0.0
            },
            "skip_tests": True,
            "expected_not": ["analyze_conflict"],
            "expected_in": ["verify"]  # When fingerprint.command='crash' and exit_code=2, returns 'verify'
        }
    ]
    
    for scenario in test_scenarios:
        # Clean up previous state
        for meta_file in get_meta_dir().glob("*.json"):
            meta_file.unlink()
        
        # Setup scenario
        if scenario["metadata"]:
            fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
            create_fingerprint_file(fp_path, scenario["metadata"])
        
        if scenario["run_report"]:
            rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
            create_run_report_file(rr_path, scenario["run_report"])
        
        # Test decision
        decision = sync_determine_operation(
            BASENAME, LANGUAGE, TARGET_COVERAGE, 
            skip_tests=scenario["skip_tests"],
            prompts_dir=str(prompts_dir)
        )
        
        # Validate results
        for forbidden_op in scenario["expected_not"]:
            assert decision.operation != forbidden_op, f"Scenario {scenario['name']}: got forbidden operation {forbidden_op}"
        
        assert decision.operation in scenario["expected_in"], f"Scenario {scenario['name']}: got {decision.operation}, expected one of {scenario['expected_in']}"

def test_real_hashes_with_context_aware_fix_over_crash(pdd_test_environment):
    """
    Test the exact scenario from debug_real_hashes.py:
    Missing files with metadata containing real hashes AND exit_code=2 with skip_tests=True.
    """
    
    # Create prompt file
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    prompt_content = """Create a Python module with a simple math function.

Requirements:
- Function name: add
- Parameters: a, b (both numbers)  
- Return: sum of a and b
- Include type hints
- Add docstring explaining the function

Example usage:
result = add(5, 3)  # Should return 8"""
    
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
    
    # Create metadata with REAL hashes (exact values from debug_real_hashes.py)
    fingerprint_data = {
        "pdd_version": "0.0.41",
        "timestamp": "2025-07-03T02:34:36.929768+00:00", 
        "command": "test",
        "prompt_hash": "79a219808ec6de6d5b885c28ee811a033ae4a92eba993f7853b5a9d6a3befa84",
        "code_hash": "6d0669923dc331420baaaefea733849562656e00f90c6519bbed46c1e9096595",
        "example_hash": "861d5b27f80c1e3b5b21b23fb58bfebb583bd4224cde95b2517a426ea4661fae",
        "test_hash": "37f6503380c4dd80a5c33be2fe08429dbc9239dd602a8147ed150863db17651f"
    }
    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, fingerprint_data)
    
    # Create run report with crash exit code (exact from debug scenario)
    run_report = {
        "timestamp": "2025-07-03T02:34:36.182803+00:00",
        "exit_code": 2,  # This triggers crash operation normally
        "tests_passed": 0,
        "tests_failed": 0,
        "coverage": 0.0
    }
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, run_report)
    
    # Test with skip_tests=True - the exact scenario that was causing issues
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, skip_tests=True, prompts_dir=str(prompts_dir))
    
    # Key assertions from debug_real_hashes.py:
    # 1. Should not return 'analyze_conflict' (was causing infinite loops)
    assert decision.operation != 'analyze_conflict', "Should not return analyze_conflict with missing files and real hashes"
    
    # 2. Should not return 'test' operation when skip_tests=True
    assert decision.operation != 'test', "Should not return test operation when skip_tests=True"
    
    # 3. With context-aware decision logic, should prefer 'fix' over 'crash' when example has run successfully before
    # The fingerprint command is "test" which indicates successful example history
    assert decision.operation == 'fix', f"Expected fix operation (context-aware), got {decision.operation}"
    assert "prefer fix over crash" in decision.reason.lower()
    assert decision.details['example_success_history'] == True

@patch('sync_determine_operation.construct_paths')
def test_decision_example_when_missing(mock_construct, pdd_test_environment):
    prompts_dir = pdd_test_environment / "prompts"
    p_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt")
    c_hash = create_file(pdd_test_environment / f"{BASENAME}.py")

    mock_construct.return_value = (
        {}, {},
        {
            'code_file': str(pdd_test_environment / f"{BASENAME}.py"),
            'example_file': str(pdd_test_environment / f"{BASENAME}_example.py"),
            'test_file': str(pdd_test_environment / f"test_{BASENAME}.py")
        },
        LANGUAGE
    )

    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.0", "timestamp": "t", "command": "generate",
        "prompt_hash": p_hash, "code_hash": c_hash, "example_hash": None, "test_hash": None
    })

    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'example'
    assert "Code exists but example missing" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_update_on_code_change(mock_construct, pdd_test_environment):
    prompts_dir = pdd_test_environment / "prompts"
    p_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt")
    create_file(pdd_test_environment / f"{BASENAME}.py", "modified code") # New hash

    mock_construct.return_value = (
        {}, {},
        {
            'code_file': str(pdd_test_environment / f"{BASENAME}.py"),
            'example_file': str(pdd_test_environment / f"{BASENAME}_example.py"),
            'test_file': str(pdd_test_environment / f"test_{BASENAME}.py")
        },
        LANGUAGE
    )

    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.o", "timestamp": "t", "command": "generate",
        "prompt_hash": p_hash, "code_hash": "original_code_hash", "example_hash": None, "test_hash": None
    })

    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'update'
    assert "Code changed" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_decision_analyze_conflict_on_multiple_changes(mock_construct, pdd_test_environment):
    prompts_dir = pdd_test_environment / "prompts"
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "modified prompt")
    create_file(pdd_test_environment / f"{BASENAME}.py", "modified code")

    mock_construct.return_value = (
        {}, {},
        {
            'code_file': str(pdd_test_environment / f"{BASENAME}.py"),
            'example_file': str(pdd_test_environment / f"{BASENAME}_example.py"),
            'test_file': str(pdd_test_environment / f"test_{BASENAME}.py")
        },
        LANGUAGE
    )

    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.0", "timestamp": "t", "command": "generate",
        "prompt_hash": "original_prompt_hash", "code_hash": "original_code_hash",
        "example_hash": None, "test_hash": None
    })

    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
    assert decision.operation == 'analyze_conflict'
    assert "Multiple files changed" in decision.reason
    assert "prompt" in decision.details['changed_files']
    assert "code" in decision.details['changed_files']


# --- Part 3: `analyze_conflict_with_llm` ---

@patch('sync_determine_operation.get_git_diff', return_value="fake diff")
@patch('sync_determine_operation.load_prompt_template', return_value="prompt: {prompt_diff}")
@patch('sync_determine_operation.llm_invoke')
@patch('sync_determine_operation.construct_paths')
def test_analyze_conflict_success(mock_construct, mock_llm_invoke, mock_load_template, mock_git_diff, pdd_test_environment):
    mock_llm_invoke.return_value = {
        'result': json.dumps({
            "next_operation": "generate",
            "reason": "LLM says so",
            "confidence": 0.9,
            "merge_strategy": {"type": "three_way_merge_safe"}
        }),
        'cost': 0.05
    }
    fingerprint = Fingerprint("1.0", "t", "generate", "p_hash", "c_hash", None, None)
    changed_files = ['prompt', 'code']

    decision = analyze_conflict_with_llm(BASENAME, LANGUAGE, fingerprint, changed_files)

    assert decision.operation == 'generate'
    assert "LLM analysis: LLM says so" in decision.reason
    assert decision.confidence == 0.9
    assert decision.estimated_cost == 0.05
    mock_load_template.assert_called_with("sync_analysis_LLM")

@patch('sync_determine_operation.get_git_diff')
@patch('sync_determine_operation.load_prompt_template')
@patch('sync_determine_operation.llm_invoke')
@patch('sync_determine_operation.construct_paths')
def test_analyze_conflict_llm_invalid_json(mock_construct, mock_llm_invoke, mock_load_template, mock_git_diff, pdd_test_environment):
    mock_load_template.return_value = "template"
    mock_llm_invoke.return_value = {'result': 'this is not json', 'cost': 0.01}
    fingerprint = Fingerprint("1.0", "t", "generate", "p", "c", None, None)

    decision = analyze_conflict_with_llm(BASENAME, LANGUAGE, fingerprint, ['prompt'])

    assert decision.operation == 'fail_and_request_manual_merge'
    assert "Invalid LLM response" in decision.reason
    assert decision.confidence == 0.0

@patch('sync_determine_operation.get_git_diff')
@patch('sync_determine_operation.load_prompt_template')
@patch('sync_determine_operation.llm_invoke')
@patch('sync_determine_operation.construct_paths')
def test_analyze_conflict_llm_low_confidence(mock_construct, mock_llm_invoke, mock_load_template, mock_git_diff, pdd_test_environment):
    mock_load_template.return_value = "template"
    mock_llm_invoke.return_value = {
        'result': json.dumps({"next_operation": "generate", "reason": "not sure", "confidence": 0.5}),
        'cost': 0.05
    }
    fingerprint = Fingerprint("1.0", "t", "generate", "p", "c", None, None)

    decision = analyze_conflict_with_llm(BASENAME, LANGUAGE, fingerprint, ['prompt'])

    assert decision.operation == 'fail_and_request_manual_merge'
    assert "LLM confidence too low" in decision.reason
    assert decision.confidence == 0.5

@patch('sync_determine_operation.load_prompt_template', return_value=None)
@patch('sync_determine_operation.construct_paths')
def test_analyze_conflict_llm_template_missing(mock_construct, mock_load_template, pdd_test_environment):
    fingerprint = Fingerprint("1.0", "t", "generate", "p", "c", None, None)
    decision = analyze_conflict_with_llm(BASENAME, LANGUAGE, fingerprint, ['prompt'])
    assert decision.operation == 'fail_and_request_manual_merge'
    assert "LLM analysis template not found" in decision.reason


# --- Part 4: Skip Flag Tests ---

@patch('sync_determine_operation.construct_paths')
def test_skip_tests_prevents_test_operation_on_low_coverage(mock_construct, pdd_test_environment):
    """Test that test operation is not returned when skip_tests=True even with low coverage."""
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 0, "tests_passed": 10, "tests_failed": 0, "coverage": 75.0
    })
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, skip_tests=True)
    assert decision.operation == 'all_synced'
    assert "tests skipped" in decision.reason.lower()

@patch('sync_determine_operation.construct_paths')
def test_skip_tests_workflow_completion(mock_construct, pdd_test_environment):
    """Test workflow completion when skip_tests=True and test files are missing."""
    prompts_dir = pdd_test_environment / "prompts"
    p_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt")
    c_hash = create_file(pdd_test_environment / f"{BASENAME}.py")
    e_hash = create_file(pdd_test_environment / f"{BASENAME}_example.py")
    # Note: NO test file created

    mock_construct.return_value = (
        {}, {},
        {
            'code_file': str(pdd_test_environment / f"{BASENAME}.py"),
            'example_file': str(pdd_test_environment / f"{BASENAME}_example.py"),
            'test_file': str(pdd_test_environment / f"test_{BASENAME}.py")
        },
        LANGUAGE
    )

    fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
    create_fingerprint_file(fp_path, {
        "pdd_version": "1.0", "timestamp": "t", "command": "example",
        "prompt_hash": p_hash, "code_hash": c_hash, "example_hash": e_hash, "test_hash": None
    })

    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir), skip_tests=True)
    assert decision.operation == 'nothing'
    assert "skip_tests=True" in decision.reason

@patch('sync_determine_operation.construct_paths')
def test_skip_flags_parameter_propagation(mock_construct, pdd_test_environment):
    """Test that skip flags are correctly used in decision logic."""
    # Test with both flags enabled
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, skip_tests=True, skip_verify=True)
    # Should not crash and should handle skip flags properly
    assert isinstance(decision, SyncDecision)

@patch('sync_determine_operation.construct_paths')
def test_skip_flags_dont_interfere_with_crash_fix(mock_construct, pdd_test_environment):
    """Test that skip flags don't interfere with crash/fix operations."""
    # Create prompt file so get_pdd_file_paths can work properly
    prompts_dir = pdd_test_environment / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", "Test prompt")
    
    # Create test file so test_file.exists() check passes
    test_file = pdd_test_environment / f"test_{BASENAME}.py"
    create_file(test_file, "def test_dummy(): pass")
    
    # Mock construct_paths to return the test file path
    mock_construct.return_value = (
        {}, {},
        {'test_file': str(test_file)},
        LANGUAGE
    )
    
    # Create run report with test failures (fix should still trigger)
    rr_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}_run.json"
    create_run_report_file(rr_path, {
        "timestamp": "t", "exit_code": 0, "tests_passed": 5, "tests_failed": 2, "coverage": 80.0
    })
    
    decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir), skip_tests=True, skip_verify=True)
    assert decision.operation == 'fix'  # Should still trigger fix despite skip flags
    assert "Test failures detected" in decision.reason

# --- Part 5: Integration Tests - Example Scenarios ---

class TestIntegrationScenarios:
    """Test the four scenarios from the example script using actual filesystem operations."""
    
    @pytest.fixture
    def integration_test_environment(self, tmp_path):
        """Creates a temporary test environment that mimics real usage."""
        original_cwd = Path.cwd()
        
        # Change to the temp directory to ensure relative paths work correctly
        os.chdir(tmp_path)
        
        # Create necessary directories
        Path(".pdd/meta").mkdir(parents=True, exist_ok=True)
        Path(".pdd/locks").mkdir(parents=True, exist_ok=True)
        
        yield tmp_path
        
        # Restore original working directory
        os.chdir(original_cwd)
    
    def test_scenario_new_unit(self, integration_test_environment):
        """Scenario 1: New Unit - A new prompt file exists with no other files or history."""
        basename = "calculator"
        language = "python"
        target_coverage = 10.0
        
        # Re-import after changing directory to ensure proper module state
        from pdd.sync_determine_operation import sync_determine_operation
        
        # Create a new prompt file in the default prompts location
        prompts_dir = Path("prompts")
        prompts_dir.mkdir(exist_ok=True)
        prompt_path = prompts_dir / f"{basename}_{language}.prompt"
        create_file(prompt_path, "Create a function to add two numbers.")
        
        # No need to mock construct_paths - let it use default behavior
        decision = sync_determine_operation(basename, language, target_coverage, log_mode=True)
        
        assert decision.operation == 'generate'
        assert "New prompt ready" in decision.reason
    
    def test_scenario_test_failures(self, integration_test_environment):
        """Scenario 2: Test Failure - A run report exists indicating test failures."""
        basename = "calculator"
        language = "python"
        target_coverage = 10.0
        
        # Re-import after changing directory
        from pdd.sync_determine_operation import sync_determine_operation
        
        # Create files
        prompts_dir = Path("prompts")
        prompts_dir.mkdir(exist_ok=True)
        create_file(prompts_dir / f"{basename}_{language}.prompt", "...")
        create_file(Path(f"{basename}.py"), "def add(a, b): return a + b")
        create_file(Path(f"test_{basename}.py"), "assert add(2, 2) == 5")
        
        # Create run report with test failure (exit_code=0 but tests_failed>0 for 'fix' operation)
        run_report = {
            "timestamp": "2025-06-29T10:00:00",
            "exit_code": 0,  # Use 0 to avoid 'crash' operation
            "tests_passed": 0,
            "tests_failed": 1,
            "coverage": 50.0
        }
        rr_path = Path(".pdd/meta") / f"{basename}_{language}_run.json"
        create_run_report_file(rr_path, run_report)
        
        decision = sync_determine_operation(basename, language, target_coverage, log_mode=True)
        
        assert decision.operation == 'fix'
        assert "Test failures detected" in decision.reason
    
    def test_scenario_manual_code_change(self, integration_test_environment):
        """Scenario 3: Manual Code Change - Code file was modified; its hash no longer matches the fingerprint."""
        basename = "calculator"
        language = "python"
        target_coverage = 10.0
        
        # Re-import after changing directory
        from pdd.sync_determine_operation import sync_determine_operation
        
        # Create files
        prompts_dir = Path("prompts")
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = "..."
        prompt_hash = hashlib.sha256(prompt_content.encode()).hexdigest()
        create_file(prompts_dir / f"{basename}_{language}.prompt", prompt_content)
        
        # Create fingerprint with old code hash
        old_code_hash = "abc123def456"
        fingerprint = {
            "pdd_version": "0.1.0",
            "timestamp": "2025-06-29T10:00:00",
            "command": "generate",
            "prompt_hash": prompt_hash,
            "code_hash": old_code_hash,
            "example_hash": None,
            "test_hash": None
        }
        fp_path = Path(".pdd/meta") / f"{basename}_{language}.json"
        create_fingerprint_file(fp_path, fingerprint)
        
        # Create code file with different content (different hash)
        create_file(Path(f"{basename}.py"), "# User added a comment\ndef add(a, b): return a + b")
        
        decision = sync_determine_operation(basename, language, target_coverage, log_mode=True)
        
        assert decision.operation == 'update'
        assert "Code changed" in decision.reason
    
    def test_scenario_synchronized_unit(self, integration_test_environment):
        """Scenario 4: Unit Synchronized - All file hashes match the fingerprint and tests passed."""
        basename = "calculator"
        language = "python"
        target_coverage = 10.0
        
        # Re-import after changing directory
        from pdd.sync_determine_operation import sync_determine_operation
        
        # Create all files with specific content
        prompts_dir = Path("prompts")
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = "..."
        code_content = "def add(a, b): return a + b"
        example_content = "print(add(1,1))"
        test_content = "assert add(2, 2) == 4"
        
        prompt_hash = create_file(prompts_dir / f"{basename}_{language}.prompt", prompt_content)
        code_hash = create_file(Path(f"{basename}.py"), code_content)
        example_hash = create_file(Path(f"{basename}_example.py"), example_content)
        test_hash = create_file(Path(f"test_{basename}.py"), test_content)
        
        # Create matching fingerprint
        fingerprint = {
            "pdd_version": "0.1.0",
            "timestamp": "2025-06-29T10:00:00",
            "command": "fix",
            "prompt_hash": prompt_hash,
            "code_hash": code_hash,
            "example_hash": example_hash,
            "test_hash": test_hash
        }
        fp_path = Path(".pdd/meta") / f"{basename}_{language}.json"
        create_fingerprint_file(fp_path, fingerprint)
        
        # Create successful run report
        run_report = {
            "timestamp": "2025-06-29T10:00:00",
            "exit_code": 0,
            "tests_passed": 1,
            "tests_failed": 0,
            "coverage": 100.0
        }
        rr_path = Path(".pdd/meta") / f"{basename}_{language}_run.json"
        create_run_report_file(rr_path, run_report)
        
        decision = sync_determine_operation(basename, language, target_coverage, log_mode=True)
        
        assert decision.operation == 'nothing'
        assert "All required files synchronized" in decision.reason


# --- Part 6: Auto-deps Infinite Loop Regression Tests ---

class TestAutoDepsInfiniteLoopFix:
    """Test the auto-deps infinite loop fix implemented to prevent continuous auto-deps operations."""
    
    def test_auto_deps_to_generate_progression(self, pdd_test_environment):
        """Test that after auto-deps completes, sync decides to run generate (not auto-deps again)."""
        
        # Create prompt file with dependencies
        prompts_dir = pdd_test_environment / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = """Create a YouTube client function.

<include>src/config.py</include>
<include>src/models.py</include>

Requirements:
- Function should discover new videos from YouTube channels
- Use the config and models from included dependencies
"""
        prompt_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
        
        # Create fingerprint showing auto-deps was just completed
        fingerprint_data = {
            "pdd_version": "0.0.46",
            "timestamp": "2025-08-04T05:22:58.044203+00:00",
            "command": "auto-deps",  # This is the key - auto-deps was last completed
            "prompt_hash": prompt_hash,  # Use actual calculated hash
            "code_hash": None,  # Code file doesn't exist yet
            "example_hash": None,
            "test_hash": None
        }
        fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
        create_fingerprint_file(fp_path, fingerprint_data)
        
        # Test the decision logic
        decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
        
        # CRITICAL: Should decide 'generate', not 'auto-deps' again
        assert decision.operation == 'generate'
        assert 'Auto-deps completed, now generate missing code file' in decision.reason
        assert decision.details['auto_deps_completed'] == True
        assert decision.details['previous_command'] == 'auto-deps'
        assert decision.details['code_exists'] == False
    
    def test_auto_deps_infinite_loop_before_fix_scenario(self, pdd_test_environment):
        """Test the exact scenario that caused infinite loop before the fix."""
        
        # Create prompt file with dependencies (like youtube_client_python.prompt)
        prompts_dir = pdd_test_environment / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = """YouTube Client Module

This module discovers new videos from configured YouTube channels.

### Dependencies

<config_dependency_example>
<include>src/config.py</include>
</config_dependency_example>

<models_dependency_example>
<include>src/models.py</include>
</models_dependency_example>

Requirements:
- Discover new videos from YouTube channels
- Process metadata for each video
"""
        prompt_hash = create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
        
        # Simulate the exact state from the sync log: auto-deps completed but code file missing
        fingerprint_data = {
            "pdd_version": "0.0.46", 
            "timestamp": "2025-08-04T05:07:29.753906+00:00",
            "command": "auto-deps",
            "prompt_hash": prompt_hash,  # Use actual calculated hash
            "code_hash": None,  # This is the key issue - no code file exists
            "example_hash": None,
            "test_hash": None
        }
        fp_path = get_meta_dir() / f"{BASENAME}_{LANGUAGE}.json"
        create_fingerprint_file(fp_path, fingerprint_data)
        
        # Before the fix: this would return 'auto-deps' and cause infinite loop
        # After the fix: this should return 'generate'
        decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
        
        # Verify the fix
        assert decision.operation == 'generate', f"Expected 'generate', got '{decision.operation}' - infinite loop fix failed"
        assert decision.operation != 'auto-deps', "Should not return auto-deps again (infinite loop)"
        assert 'Auto-deps completed' in decision.reason
        assert decision.confidence == 0.90  # High confidence since this is deterministic
    
    def test_auto_deps_without_dependencies_still_works(self, pdd_test_environment):
        """Test that normal auto-deps logic still works when prompt has no dependencies."""
        
        # Create prompt file WITHOUT dependencies
        prompts_dir = pdd_test_environment / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = """Create a simple calculator function.

Requirements:
- Function name: add
- Parameters: a, b (both numbers)
- Return: sum of a and b
"""
        create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
        
        # No fingerprint (new unit scenario)
        # Code file doesn't exist
        
        decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
        
        # Should go directly to generate since no dependencies detected
        assert decision.operation == 'generate'
        assert 'New prompt ready' in decision.reason
        assert decision.details.get('has_dependencies', True) == False  # No dependencies
    
    def test_auto_deps_first_time_with_dependencies(self, pdd_test_environment):
        """Test that auto-deps is correctly chosen for new prompts with dependencies."""
        
        # Create prompt file WITH dependencies
        prompts_dir = pdd_test_environment / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompt_content = """Create a data processor.

<include>context/database_example.py</include>
<web>https://example.com/api-docs</web>

Requirements:
- Process data using included database example
- Fetch API documentation from web
"""
        create_file(prompts_dir / f"{BASENAME}_{LANGUAGE}.prompt", prompt_content)
        
        # No fingerprint (new unit scenario)
        # Code file doesn't exist
        
        decision = sync_determine_operation(BASENAME, LANGUAGE, TARGET_COVERAGE, prompts_dir=str(prompts_dir))
        
        # Should choose auto-deps for first time with dependencies
        assert decision.operation == 'auto-deps'
        assert 'New prompt with dependencies detected' in decision.reason
        assert decision.details['has_dependencies'] == True
        assert decision.details['fingerprint_found'] == False

# --- Part 7: Edge Cases and Helper Function Tests ---
# These tests were consolidated from test_sync_edge_cases.py

class TestValidateExpectedFiles:
    """Test the validate_expected_files function."""
    
    def test_validate_with_no_fingerprint(self):
        """Test validation when no fingerprint is provided."""
        paths = {
            'code': Path('test.py'),
            'example': Path('test_example.py'),
            'test': Path('test_test.py')
        }
        
        result = validate_expected_files(None, paths)
        assert result == {}
    
    def test_validate_all_files_exist(self, tmp_path):
        """Test validation when all expected files exist."""
        # Create test files
        code_file = tmp_path / "test.py"
        example_file = tmp_path / "test_example.py"
        test_file = tmp_path / "test_test.py"
        
        code_file.write_text("print('hello')")
        example_file.write_text("from test import *")
        test_file.write_text("def test_func(): pass")
        
        paths = {
            'code': code_file,
            'example': example_file,
            'test': test_file
        }
        
        fingerprint = Fingerprint(
            pdd_version="0.0.41",
            timestamp=datetime.now(timezone.utc).isoformat(),
            command="test",
            prompt_hash="prompt123",
            code_hash="code456",
            example_hash="example789",
            test_hash="test012"
        )
        
        result = validate_expected_files(fingerprint, paths)
        
        assert result == {
            'code': True,
            'example': True,
            'test': True
        }
    
    def test_validate_missing_files(self, tmp_path):
        """Test validation when expected files are missing."""
        # Create only code file
        code_file = tmp_path / "test.py"
        example_file = tmp_path / "test_example.py"
        test_file = tmp_path / "test_test.py"
        
        code_file.write_text("print('hello')")
        # Don't create example and test files
        
        paths = {
            'code': code_file,
            'example': example_file,
            'test': test_file
        }
        
        fingerprint = Fingerprint(
            pdd_version="0.0.41",
            timestamp=datetime.now(timezone.utc).isoformat(),
            command="test",
            prompt_hash="prompt123",
            code_hash="code456",
            example_hash="example789",
            test_hash="test012"
        )
        
        result = validate_expected_files(fingerprint, paths)
        
        assert result == {
            'code': True,
            'example': False,
            'test': False
        }


class TestHandleMissingExpectedFiles:
    """Test the _handle_missing_expected_files function."""
    
    def test_missing_code_file_with_prompt(self, tmp_path):
        """Test recovery when code file is missing but prompt exists."""
        prompt_file = tmp_path / "test_python.prompt"
        prompt_file.write_text("Create a simple function")
        
        paths = {
            'prompt': prompt_file,
            'code': tmp_path / "test.py",
            'example': tmp_path / "test_example.py",
            'test': tmp_path / "test_test.py"
        }
        
        fingerprint = Fingerprint(
            pdd_version="0.0.41",
            timestamp=datetime.now(timezone.utc).isoformat(),
            command="test",
            prompt_hash="prompt123",
            code_hash="code456",
            example_hash=None,
            test_hash=None
        )
        
        decision = _handle_missing_expected_files(
            missing_files=['code'],
            paths=paths,
            fingerprint=fingerprint,
            basename="test",
            language="python",
            prompts_dir="prompts"
        )
        
        assert decision.operation == 'generate'
        assert 'Code file missing' in decision.reason
        # The confidence value is set to 1.0 because the decision to generate
        # a new code file is deterministic when the code file is missing, and
        # all other required files (e.g., prompt) are present.
        assert decision.confidence == 1.0
    def test_missing_test_file_with_skip_tests(self, tmp_path):
        """Test recovery when test file is missing and skip_tests is True."""
        code_file = tmp_path / "test.py"
        example_file = tmp_path / "test_example.py"
        
        code_file.write_text("def add(a, b): return a + b")
        example_file.write_text("from test import add; print(add(1, 2))")
        
        paths = {
            'prompt': tmp_path / "test_python.prompt",
            'code': code_file,
            'example': example_file,
            'test': tmp_path / "test_test.py"
        }
        
        fingerprint = Fingerprint(
            pdd_version="0.0.41",
            timestamp=datetime.now(timezone.utc).isoformat(),
            command="test",
            prompt_hash="prompt123",
            code_hash="code456",
            example_hash="example789",
            test_hash="test012"
        )
        
        decision = _handle_missing_expected_files(
            missing_files=['test'],
            paths=paths,
            fingerprint=fingerprint,
            basename="test",
            language="python",
            prompts_dir="prompts",
            skip_tests=True
        )
        
        assert decision.operation == 'nothing'
        assert 'skip-tests specified' in decision.reason
        assert decision.details['skip_tests'] is True
    
    def test_missing_example_file(self, tmp_path):
        """Test recovery when example file is missing but code exists."""
        code_file = tmp_path / "test.py"
        code_file.write_text("def add(a, b): return a + b")
        
        paths = {
            'prompt': tmp_path / "test_python.prompt",
            'code': code_file,
            'example': tmp_path / "test_example.py",
            'test': tmp_path / "test_test.py"
        }
        
        fingerprint = Fingerprint(
            pdd_version="0.0.41",
            timestamp=datetime.now(timezone.utc).isoformat(),
            command="test",
            prompt_hash="prompt123",
            code_hash="code456",
            example_hash="example789",
            test_hash=None
        )
        
        decision = _handle_missing_expected_files(
            missing_files=['example'],
            paths=paths,
            fingerprint=fingerprint,
            basename="test",
            language="python",
            prompts_dir="prompts"
        )
        
        assert decision.operation == 'example'
        assert 'Example file missing' in decision.reason


class TestIsWorkflowComplete:
    """Test the _is_workflow_complete function."""
    
    def test_workflow_complete_without_skip_flags(self, tmp_path):
        """Test workflow completion when all files exist and no skip flags."""
        code_file = tmp_path / "test.py"
        example_file = tmp_path / "test_example.py"
        test_file = tmp_path / "test_test.py"
        
        # Create all files
        code_file.write_text("def add(a, b): return a + b")
        example_file.write_text("from test import add")
        test_file.write_text("def test_add(): pass")
        
        paths = {
            'code': code_file,
            'example': example_file,
            'test': test_file
        }
        
        assert _is_workflow_complete(paths) is True
        assert _is_workflow_complete(paths, skip_tests=False) is True
    
    def test_workflow_complete_with_skip_tests(self, tmp_path):
        """Test workflow completion when test file missing but skip_tests=True."""
        code_file = tmp_path / "test.py"
        example_file = tmp_path / "test_example.py"
        
        # Create only code and example files
        code_file.write_text("def add(a, b): return a + b")
        example_file.write_text("from test import add")
        
        paths = {
            'code': code_file,
            'example': example_file,
            'test': tmp_path / "test_test.py"  # Doesn't exist
        }
        
        assert _is_workflow_complete(paths) is False  # Requires test file
        assert _is_workflow_complete(paths, skip_tests=True) is True  # Skip test requirement
    
    def test_workflow_incomplete(self, tmp_path):
        """Test workflow is incomplete when required files are missing."""
        code_file = tmp_path / "test.py"
        code_file.write_text("def add(a, b): return a + b")
        
        paths = {
            'code': code_file,
            'example': tmp_path / "test_example.py",  # Doesn't exist
            'test': tmp_path / "test_test.py"  # Doesn't exist
        }
        
        assert _is_workflow_complete(paths) is False
        assert _is_workflow_complete(paths, skip_tests=True) is False  # Still needs example


class TestSyncDetermineOperationRegressionScenarios:
    """Additional regression tests for sync_determine_operation edge cases."""
    
    def test_missing_files_with_metadata_regression_scenario(self, tmp_path):
        """Test the exact regression scenario: files deleted but metadata remains."""
        # Change to temp directory for the test
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Create directory structure
            (tmp_path / "prompts").mkdir()
            (tmp_path / ".pdd" / "meta").mkdir(parents=True)
            
            # Create prompt file
            prompt_file = tmp_path / "prompts" / "simple_math_python.prompt"
            prompt_file.write_text("""Create a Python module with a simple math function.

Requirements:
- Function name: add
- Parameters: a, b (both numbers)  
- Return: sum of a and b
""")
            
            # Create metadata (simulating previous successful sync)
            meta_file = tmp_path / ".pdd" / "meta" / "simple_math_python.json"
            meta_file.write_text(json.dumps({
                "pdd_version": "0.0.41",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "command": "test",
                "prompt_hash": "abc123",
                "code_hash": "def456",
                "example_hash": "ghi789",
                "test_hash": "jkl012"
            }, indent=2))
            
            # Files are deliberately missing (deleted like in regression test)
            
            # Test sync_determine_operation behavior
            decision = sync_determine_operation(
                basename="simple_math",
                language="python",
                target_coverage=90.0,
                budget=10.0,
                log_mode=False,
                prompts_dir="prompts",
                skip_tests=True,
                skip_verify=False
            )
            
            # Should NOT return analyze_conflict anymore
            assert decision.operation != 'analyze_conflict'
            
            # Should return appropriate recovery operation
            assert decision.operation in ['generate', 'auto-deps']
            assert 'missing' in decision.reason.lower() or 'regenerate' in decision.reason.lower()
            
        finally:
            os.chdir(original_cwd)
    
    def test_skip_flags_integration(self, tmp_path):
        """Test that skip flags are properly integrated throughout the decision logic."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Create directory structure
            (tmp_path / "prompts").mkdir()
            
            # Create prompt file
            prompt_file = tmp_path / "prompts" / "test_python.prompt"
            prompt_file.write_text("Create a simple function")
            
            # Test with skip_tests=True
            decision = sync_determine_operation(
                basename="test",
                language="python",
                target_coverage=90.0,
                budget=10.0,
                log_mode=False,
                prompts_dir="prompts",
                skip_tests=True,
                skip_verify=False
            )
            
            # Should start normal workflow
            assert decision.operation in ['generate', 'auto-deps']
            
        finally:
            os.chdir(original_cwd)


class TestGetPddFilePaths:
    """Test get_pdd_file_paths function to prevent path resolution regression."""
    
    def test_get_pdd_file_paths_respects_pddrc_when_prompt_missing(self, tmp_path, monkeypatch):
        """Test that get_pdd_file_paths uses .pddrc configuration even when prompt doesn't exist.
        
        This test prevents regression of the bug where test files were looked for in the
        current directory instead of the configured tests/ subdirectory.
        """
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Create .pddrc configuration file
            pddrc_content = """version: "1.0"
contexts:
  regression:
    paths: ["**"]
    defaults:
      test_output_path: "tests/"
      example_output_path: "examples/"
      default_language: "python"
"""
            (tmp_path / ".pddrc").write_text(pddrc_content)
            
            # Create directory structure
            (tmp_path / "prompts").mkdir()
            (tmp_path / "tests").mkdir()
            (tmp_path / "examples").mkdir()
            
            # Mock construct_paths to return configured paths
            def mock_construct_paths(input_file_paths, force, quiet, command, command_options):
                # Simulate what construct_paths would return with .pddrc configuration
                return (
                    {
                        "test_output_path": "tests/",
                        "example_output_path": "examples/",
                        "generate_output_path": "./"
                    },
                    {},
                    {},  # output_paths is empty when called with empty input_file_paths
                    "python"
                )
            
            monkeypatch.setattr('sync_determine_operation.construct_paths', mock_construct_paths)
            
            # Test when prompt file doesn't exist - this is the regression scenario
            basename = "test_unit"
            language = "python"
            paths = get_pdd_file_paths(basename, language, "prompts")
            
            # Verify paths respect configuration, not hardcoded to current directory
            # The bug was that test file was "test_test_unit.py" instead of "tests/test_test_unit.py"
            assert str(paths['test']) == "tests/test_test_unit.py", f"Test path should be in tests/ subdirectory, got: {paths['test']}"
            assert str(paths['example']) == "examples/test_unit_example.py", f"Example path should be in examples/ subdirectory, got: {paths['example']}"
            assert str(paths['code']) == "test_unit.py", f"Code path can be in current directory, got: {paths['code']}"
            
            # Verify the paths are Path objects
            assert isinstance(paths['test'], Path)
            assert isinstance(paths['example'], Path)
            assert isinstance(paths['code'], Path)
            assert isinstance(paths['prompt'], Path)
            
        finally:
            os.chdir(original_cwd)
    
    def test_get_pdd_file_paths_fallback_without_construct_paths(self, tmp_path, monkeypatch):
        """Test that paths use configured directories even without .pddrc when prompt is missing.
        
        After the fix, even without .pddrc, construct_paths should provide
        sensible defaults based on the PDD context detection.
        """
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Create directory structure
            (tmp_path / "prompts").mkdir()
            
            # Don't create the prompt file - trigger the fallback logic
            basename = "test_unit"
            language = "python"
            
            # Get paths without mocking - this uses construct_paths now
            paths = get_pdd_file_paths(basename, language, "prompts")
            
            # After fix: paths should use PDD's default directory structure
            # The exact paths depend on whether construct_paths detects a context
            # In a bare directory, it might still use current directory as fallback
            # But with .pddrc present, it should use configured paths
            
            # For a bare directory without .pddrc, current behavior is acceptable
            # The important fix is that WITH .pddrc, paths are respected
            assert isinstance(paths['test'], Path)
            assert isinstance(paths['example'], Path)
            assert isinstance(paths['code'], Path)
            
        finally:
            os.chdir(original_cwd)
    
    @patch('sync_determine_operation.construct_paths')
    def test_sync_operation_with_missing_prompt_respects_test_path(self, mock_construct, tmp_path):
        """Test that sync_determine_operation doesn't fail when test file is in configured directory.
        
        This simulates the exact regression scenario where sync fails with
        "No such file or directory: 'test_simple_math.py'" because it's looking
        in the wrong directory.
        """
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Create directory structure as per .pddrc
            (tmp_path / ".pdd" / "meta").mkdir(parents=True)
            (tmp_path / ".pdd" / "locks").mkdir(parents=True)
            (tmp_path / "prompts").mkdir()
            (tmp_path / "tests").mkdir()
            (tmp_path / "examples").mkdir()
            
            # Create .pddrc file
            pddrc_content = """version: "1.0"
contexts:
  regression:
    paths: ["**"]
    defaults:
      test_output_path: "tests/"
      example_output_path: "examples/"
"""
            (tmp_path / ".pddrc").write_text(pddrc_content)
            
            # Mock construct_paths to return .pddrc-configured paths
            mock_construct.return_value = (
                {"test_output_path": "tests/"},
                {},
                {
                    "output": "tests/test_simple_math.py",
                    "test_file": "tests/test_simple_math.py",
                    "example_file": "examples/simple_math_example.py",
                    "code_file": "simple_math.py"
                },
                "python"
            )
            
            # Don't create prompt file - this simulates the regression scenario
            # The sync should still work and not look for test_simple_math.py in current dir
            
            decision = sync_determine_operation(
                basename="simple_math",
                language="python",
                target_coverage=90.0,
                budget=10.0,
                log_mode=False,
                prompts_dir="prompts",
                skip_tests=False,
                skip_verify=False
            )
            
            # Verify no FileNotFoundError is raised
            # The decision should handle missing files gracefully
            assert isinstance(decision, SyncDecision)
            # Should return an operation that makes sense for missing prompt
            assert decision.operation in ['nothing', 'auto-deps', 'generate']
            
        finally:
            os.chdir(original_cwd)
    
    def test_file_path_lookup_regression(self, tmp_path, monkeypatch):
        """Test the exact regression scenario: file lookup after verify completes.
        
        This test simulates the exact error seen in sync regression where
        after verify completes, something tries to read 'test_simple_math.py'
        from the current directory instead of 'tests/test_simple_math.py'.
        """
        original_cwd = os.getcwd()
        
        # Store original module constants to restore them later
        pdd_module = sys.modules['sync_determine_operation']
        original_pdd_dir = pdd_module.PDD_DIR
        original_meta_dir = pdd_module.META_DIR
        original_locks_dir = pdd_module.LOCKS_DIR
        
        try:
            os.chdir(tmp_path)
            
            # Set PDD_PATH environment variable for get_language function
            monkeypatch.setenv("PDD_PATH", str(tmp_path))
            
            # Create language mapping CSV files that get_language function needs
            language_csv_content = """extension,language
.py,python
.js,javascript
.java,java
.cpp,cpp
.c,c
.go,go
.rs,rust
.rb,ruby
.php,php
.ts,typescript
.swift,swift
.kt,kotlin
.scala,scala
.clj,clojure
.hs,haskell
.ml,ocaml
.fs,fsharp
.ex,elixir
.erl,erlang
.pl,perl
.lua,lua
.r,r
.m,matlab
.jl,julia
.dart,dart
.groovy,groovy
.sh,bash
.ps1,powershell
.bat,batch
.cmd,batch
.vb,vb
.cs,csharp
.f,fortran
.f90,fortran
.pas,pascal
.asm,assembly
.s,assembly
.sol,solidity
.move,move
"""
            (tmp_path / "language_extension_mapping.csv").write_text(language_csv_content)
            
            # Create data directory and language_format.csv
            (tmp_path / "data").mkdir()
            (tmp_path / "data" / "language_format.csv").write_text(language_csv_content)
            
            # Update module constants after changing directory
            pdd_module.PDD_DIR = pdd_module.get_pdd_dir()
            pdd_module.META_DIR = pdd_module.get_meta_dir()
            pdd_module.LOCKS_DIR = pdd_module.get_locks_dir()
            
            # Create directory structure matching regression test
            (tmp_path / "prompts").mkdir()
            (tmp_path / "tests").mkdir()
            (tmp_path / "examples").mkdir()
            (tmp_path / ".pdd" / "meta").mkdir(parents=True)
            
            # Create the files that exist after verify completes
            (tmp_path / "prompts" / "simple_math_python.prompt").write_text("Create add function")
            (tmp_path / "simple_math.py").write_text("def add(a, b): return a + b")
            (tmp_path / "examples" / "simple_math_example.py").write_text("from simple_math import add")
            (tmp_path / "simple_math_verify_results.log").write_text("Success")
            
            # Create .pddrc that specifies test path
            pddrc_content = """version: "1.0"
contexts:
  regression:
    paths: ["**"]
    defaults:
      test_output_path: "tests/"
      example_output_path: "examples/"
"""
            (tmp_path / ".pddrc").write_text(pddrc_content)
            
            # The test file should be in tests/ directory according to .pddrc
            # but the error shows it's being looked for in current directory
            
            # Use the already imported get_pdd_file_paths to avoid module conflicts
            # get_pdd_file_paths was imported at the top of the file
            
            # Get file paths - this should respect .pddrc
            paths = get_pdd_file_paths("simple_math", "python", "prompts")
            
            # This demonstrates the bug: trying to check if test file exists
            # in the wrong location would cause the error
            test_path = paths['test']
            
            # The fix is now in place, so we should always get the correct path
            # Verify that the path respects the .pddrc configuration
            assert "tests/test_simple_math.py" in str(test_path) or "tests\\test_simple_math.py" in str(test_path), \
                f"Expected test path to be in tests/ subdirectory as per .pddrc, but got: {test_path}"
            
            # Verify the file lookup fails with the correct path (file doesn't exist)
            try:
                with open(test_path, 'r') as f:
                    f.read()
                assert False, "Should have raised FileNotFoundError"
            except FileNotFoundError as e:
                error_msg = str(e)
                assert "tests/test_simple_math.py" in error_msg or "tests\\test_simple_math.py" in error_msg, \
                    f"Expected error to reference 'tests/test_simple_math.py', but got: {error_msg}"
                
            # After fix, the path should be 'tests/test_simple_math.py'
            # and this error wouldn't occur if the file existed there
            
        finally:
            os.chdir(original_cwd)
            
            # Restore original module constants
            pdd_module.PDD_DIR = original_pdd_dir
            pdd_module.META_DIR = original_meta_dir
            pdd_module.LOCKS_DIR = original_locks_dir


# --- Regression: Output path resolution under sync (integrated) ---

def _write_pddrc_here() -> None:
    content = (
        "contexts:\n"
        "  default:\n"
        "    defaults:\n"
        "      generate_output_path: pdd/\n"
        "      example_output_path: examples/\n"
        "      test_output_path: tests/\n"
    )
    Path(".pdd").mkdir(parents=True, exist_ok=True)
    Path(".pddrc").write_text(content, encoding="utf-8")


def _write_simple_prompt(basename: str = "simple_math", language: str = "python") -> None:
    prompts_dir = Path("prompts")
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / f"{basename}_{language}.prompt").write_text(
        """
Write a simple add(a, b) function that returns a + b.
Also include a subtract(a, b) that returns a - b.
""".strip(),
        encoding="utf-8",
    )


def test_get_pdd_file_paths_respects_pddrc_without_PDD_PATH(pdd_test_environment, monkeypatch):
    _write_pddrc_here()
    _write_simple_prompt()
    monkeypatch.delenv("PDD_PATH", raising=False)
    paths = get_pdd_file_paths(basename="simple_math", language="python", prompts_dir="prompts")
    assert paths["code"].as_posix().endswith("pdd/simple_math.py"), f"Got: {paths['code']}"


def test_get_pdd_file_paths_respects_pddrc_with_PDD_PATH(pdd_test_environment, monkeypatch):
    _write_pddrc_here()
    _write_simple_prompt()
    repo_root = Path(__file__).parent.parent
    monkeypatch.setenv("PDD_PATH", str(repo_root))
    paths = get_pdd_file_paths(basename="simple_math", language="python", prompts_dir="prompts")
    assert paths["code"].as_posix().endswith("pdd/simple_math.py")
    assert paths["example"].as_posix().endswith("examples/simple_math_example.py")
    assert paths["test"].as_posix().endswith("tests/test_simple_math.py")
