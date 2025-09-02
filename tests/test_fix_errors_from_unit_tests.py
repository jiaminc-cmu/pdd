import pytest
from pathlib import Path
import tempfile
import os
from unittest.mock import patch, MagicMock
from pydantic import BaseModel

from pdd.fix_errors_from_unit_tests import fix_errors_from_unit_tests, CodeFix

# Test data
SAMPLE_UNIT_TEST = """
def test_example():
    assert 1 + 1 == 2
"""

SAMPLE_CODE = """
def add(a, b):
    return a + b
"""

SAMPLE_PROMPT = """
Write a function that adds two numbers.
"""

SAMPLE_ERROR = "AssertionError: assert 2 == 3"

@pytest.fixture
def temp_error_file():
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
        yield f.name
    os.unlink(f.name)

@pytest.fixture
def mock_llm_invoke():
    with patch('pdd.fix_errors_from_unit_tests.llm_invoke') as mock:
        yield mock

@pytest.fixture
def mock_load_prompt_template():
    with patch('pdd.fix_errors_from_unit_tests.load_prompt_template') as mock:
        mock.return_value = "mock prompt template"
        yield mock

def test_successful_fix(temp_error_file, mock_llm_invoke, mock_load_prompt_template):
    # Mock responses
    mock_llm_invoke.side_effect = [
        {
            'result': "Analysis of the error...",
            'cost': 0.001,
            'model_name': "gpt-3.5-turbo"
        },
        {
            'result': CodeFix(
                update_unit_test=True,
                update_code=False,
                fixed_unit_test="def test_example_fixed():\n    assert 1 + 1 == 3",
                fixed_code=""
            ),
            'cost': 0.002,
            'model_name': "gpt-3.5-turbo"
        }
    ]

    result = fix_errors_from_unit_tests(
        unit_test=SAMPLE_UNIT_TEST,
        code=SAMPLE_CODE,
        prompt=SAMPLE_PROMPT,
        error=SAMPLE_ERROR,
        error_file=temp_error_file,
        strength=0.7,
        temperature=0.5,
        verbose=True
    )

    assert isinstance(result, tuple)
    assert len(result) == 7
    update_unit_test, update_code, fixed_unit_test, fixed_code, analysis_results, total_cost, model_name = result
    
    assert update_unit_test is True
    assert update_code is False
    assert fixed_unit_test == "def test_example_fixed():\n    assert 1 + 1 == 3"
    assert fixed_code == ""
    assert analysis_results == "Analysis of the error..."
    assert total_cost == 0.003
    assert model_name == "gpt-3.5-turbo"

def test_missing_prompt_templates(temp_error_file, mock_load_prompt_template):
    mock_load_prompt_template.return_value = None

    result = fix_errors_from_unit_tests(
        unit_test=SAMPLE_UNIT_TEST,
        code=SAMPLE_CODE,
        prompt=SAMPLE_PROMPT,
        error=SAMPLE_ERROR,
        error_file=temp_error_file,
        strength=0.7,
        temperature=0.5
    )

    assert result == (False, False, "", "", "", 0.0, "")

def test_llm_invoke_error(temp_error_file, mock_llm_invoke, mock_load_prompt_template):
    mock_llm_invoke.side_effect = Exception("LLM API error")

    result = fix_errors_from_unit_tests(
        unit_test=SAMPLE_UNIT_TEST,
        code=SAMPLE_CODE,
        prompt=SAMPLE_PROMPT,
        error=SAMPLE_ERROR,
        error_file=temp_error_file,
        strength=0.7,
        temperature=0.5
    )

    assert result == (False, False, "", "", "", 0.0, "")

def test_invalid_error_file_path(mock_llm_invoke, mock_load_prompt_template, capsys):
    invalid_path = "/nonexistent/directory/errors.log"

    # Mock responses similar to successful fix, just to test error file handling
    mock_llm_invoke.side_effect = [
        {
            'result': "Analysis for invalid path test",
            'cost': 0.001,
            'model_name': "gpt-3.5-turbo"
        },
        {
            'result': CodeFix(
                update_unit_test=True,
                update_code=False,
                fixed_unit_test="fixed test for invalid path",
                fixed_code=""
            ),
            'cost': 0.002,
            'model_name': "gpt-3.5-turbo"
        }
    ]

    result = fix_errors_from_unit_tests(
        unit_test=SAMPLE_UNIT_TEST,
        code=SAMPLE_CODE,
        prompt=SAMPLE_PROMPT,
        error=SAMPLE_ERROR,
        error_file=invalid_path,
        strength=0.7,
        temperature=0.5,
        verbose=True
    )

    captured = capsys.readouterr()
    assert "Running fix_errors_from_unit_tests" in captured.out
    assert "Running extract_unit_code_fix" in captured.out
    assert "Total cost" in captured.out

    # Verify the result structure
    assert isinstance(result, tuple)
    assert len(result) == 7
    update_unit_test, update_code, fixed_unit_test, fixed_code, analysis_results, total_cost, model_name = result
    assert isinstance(analysis_results, str)
    assert total_cost == 0.003
    assert model_name == "gpt-3.5-turbo"

def test_input_validation():
    with pytest.raises(ValueError):
        fix_errors_from_unit_tests(
            unit_test="",  # Empty unit test
            code=SAMPLE_CODE,
            prompt=SAMPLE_PROMPT,
            error=SAMPLE_ERROR,
            error_file="test.log",
            strength=0.7,
            temperature=0.5
        )
    with pytest.raises(ValueError):
        fix_errors_from_unit_tests(
            unit_test=SAMPLE_UNIT_TEST,
            code=SAMPLE_CODE,
            prompt=SAMPLE_PROMPT,
            error=SAMPLE_ERROR,
            error_file="test.log",
            strength=1.5,  # Invalid strength
            temperature=0.5
        )
    with pytest.raises(ValueError):
        fix_errors_from_unit_tests(
            unit_test=SAMPLE_UNIT_TEST,
            code=SAMPLE_CODE,
            prompt=SAMPLE_PROMPT,
            error=SAMPLE_ERROR,
            error_file="test.log",
            strength=0.7,
            temperature=2.0  # Invalid temperature
        )

def test_verbose_output(temp_error_file, mock_llm_invoke, mock_load_prompt_template, capsys):
    mock_llm_invoke.side_effect = [
        {
            'result': "Analysis of the error...",
            'cost': 0.001,
            'model_name': "gpt-3.5-turbo"
        },
        {
            'result': CodeFix(
                update_unit_test=True,
                update_code=False,
                fixed_unit_test="fixed test",
                fixed_code=""
            ),
            'cost': 0.002,
            'model_name': "gpt-3.5-turbo"
        }
    ]

    result = fix_errors_from_unit_tests(
        unit_test=SAMPLE_UNIT_TEST,
        code=SAMPLE_CODE,
        prompt=SAMPLE_PROMPT,
        error=SAMPLE_ERROR,
        error_file=temp_error_file,
        strength=0.7,
        temperature=0.5,
        verbose=True
    )

    captured = capsys.readouterr()
    assert "Running fix_errors_from_unit_tests" in captured.out
    assert "Running extract_unit_code_fix" in captured.out
    assert "Total cost" in captured.out

    # Verify the result structure
    assert len(result) == 7
    update_unit_test, update_code, fixed_unit_test, fixed_code, analysis_results, total_cost, model_name = result
    assert isinstance(analysis_results, str)
    assert update_unit_test is True
    assert fixed_unit_test == "fixed test"
    assert analysis_results == "Analysis of the error..."
    assert total_cost == 0.003
    assert model_name == "gpt-3.5-turbo"

def test_import_path_modification_in_unit_test(temp_error_file, mock_llm_invoke, mock_load_prompt_template):
    """
    Test that the function correctly identifies and returns a fix for import paths
    in decorator statements within the unit test itself.
    """
    # Setup a unit test with an incorrect mock patch decorator target
    test_with_incorrect_patch = """
import os
import pytest
import pandas as pd
from unittest.mock import patch
from pdd_wrapper import get_extension

class TestGetExtension:
    @patch("pd.read_csv")
    def test_csv_empty(self, mock_read_csv):
        mock_read_csv.side_effect = pd.errors.EmptyDataError
        result = get_extension("Python")
        assert result == ""
"""

    # Code implementation remains the same
    code_implementation = """
import os
import pandas as pd
from rich.console import Console

console = Console()

def get_extension(language):
    if language is None or not isinstance(language, str):
        console.print("Error: Language parameter must be a valid string")
        return ""
    
    # Rest of implementation
    return ".py"
"""

    # Error message showing the import path issue
    error_message = """
E       ModuleNotFoundError: No module named 'pd'
"""

    # Expected analysis from the first LLM call identifying the incorrect patch target
    llm_analysis_identifying_patch_issue = """
Analysis: The unit test uses @patch("pd.read_csv"), but the target should be 'pdd_wrapper.pd.read_csv'
because that's how it's imported and used within the pdd_wrapper module being tested.
"""
    # Expected corrected unit test string
    corrected_unit_test_string = """
import os
import pytest
import pandas as pd
from unittest.mock import patch
from pdd_wrapper import get_extension

class TestGetExtension:
    @patch("pdd_wrapper.pd.read_csv") # Corrected path
    def test_csv_empty(self, mock_read_csv):
        mock_read_csv.side_effect = pd.errors.EmptyDataError
        result = get_extension("Python")
        assert result == ""
"""

    # Mock the LLM responses
    mock_llm_invoke.side_effect = [
        # First call (fix_errors_from_unit_tests_LLM)
        {
            'result': llm_analysis_identifying_patch_issue,
            'cost': 0.001,
            'model_name': "model-1"
        },
        # Second call (extract_unit_code_fix_LLM)
        {
            'result': CodeFix(
                update_unit_test=True,       # Indicate unit test should be updated
                update_code=False,           # Indicate code under test is fine
                fixed_unit_test=corrected_unit_test_string, # Provide the fixed test
                fixed_code=""                # No change to code under test
            ),
            'cost': 0.002,
            'model_name': "model-2" # Can be the same or different
        }
    ]

    # Run the fix_errors_from_unit_tests function
    result = fix_errors_from_unit_tests(
        unit_test=test_with_incorrect_patch,
        code=code_implementation,
        prompt="Create a get_extension function",
        error=error_message,
        error_file=temp_error_file,
        strength=0.7,
        temperature=0.5,
        verbose=True
    )

    # Verify the results
    assert isinstance(result, tuple)
    assert len(result) == 7
    update_unit_test, update_code, fixed_unit_test, fixed_code, analysis, cost, model = result

    # Assert that the function correctly determined the unit test needs updating
    assert update_unit_test is True
    assert update_code is False

    # Assert that the returned fixed_unit_test contains the corrected patch path
    assert "@patch(\"pdd_wrapper.pd.read_csv\")" in fixed_unit_test
    assert "@patch(\"pd.read_csv\")" not in fixed_unit_test # Ensure original is gone

    # Assert other return values
    assert fixed_code == "" # No code changes expected
    assert analysis == llm_analysis_identifying_patch_issue
    assert cost == 0.003
    assert model == "model-1" # Model name from the first call is returned

def test_regression_reproduction_with_mocks(temp_error_file, mock_llm_invoke, mock_load_prompt_template):
    """
    This test reproduces the scenario from the original regression test,
    but uses mocks to ensure the expected behavior (fixing the unit test patch)
    is correctly handled and returned by fix_errors_from_unit_tests.
    """
    # Original unit test content with incorrect patch
    test_content = """
import os
import pytest
import pandas as pd
from unittest.mock import patch
from pdd_wrapper import get_extension

class TestGetExtension:
    # ... other tests ...
    @patch("os.path.join")
    @patch("pd.read_csv") # Incorrect patch target
    def test_empty_csv(self, mock_read_csv, mock_join):
        mock_join.return_value = "/fake/path/to/language_format.csv"
        mock_read_csv.side_effect = pd.errors.EmptyDataError
        result = get_extension("Python")
        assert result == ""
"""
    # Original code content
    code_content = """
import os
import pandas as pd
from rich.console import Console

console = Console()

def get_extension(language):
    # ... implementation ...
    return ".py" # Simplified for test
"""
    # Error log
    error_log = "E       ModuleNotFoundError: No module named 'pd'"

    # Analysis result from the first LLM call identifying the patch issue
    analysis_result = "Analysis: The patch target 'pd.read_csv' should be 'pdd_wrapper.pd.read_csv'."

    # The correctly fixed unit test string
    correctly_fixed_unit_test = """
import os
import pytest
import pandas as pd
from unittest.mock import patch
from pdd_wrapper import get_extension

class TestGetExtension:
    # ... other tests ...
    @patch("os.path.join")
    @patch("pdd_wrapper.pd.read_csv") # Corrected patch target
    def test_empty_csv(self, mock_read_csv, mock_join):
        mock_join.return_value = "/fake/path/to/language_format.csv"
        mock_read_csv.side_effect = pd.errors.EmptyDataError
        result = get_extension("Python")
        assert result == ""
"""

    # --- Mock Setup ---
    mock_load_prompt_template.return_value = "Mocked Template" # Ensure templates load

    mock_llm_invoke.side_effect = [
        # Response for the first LLM call (analysis)
        {
            'result': analysis_result,
            'cost': 0.001,
            'model_name': "claude-analysis"
        },
        # Response for the second LLM call (extraction)
        # Simulate the LLM correctly identifying the test needs fixing
        {
            'result': CodeFix(
                update_unit_test=True,
                update_code=False,
                fixed_unit_test=correctly_fixed_unit_test,
                fixed_code=""
            ),
            'cost': 0.002,
            'model_name': "gemini-extract" # Can be different
        }
    ]
    # --- End Mock Setup ---

    # Run the function under test
    result = fix_errors_from_unit_tests(
        unit_test=test_content,
        code=code_content,
        prompt="Test prompt",
        error=error_log,
        error_file=temp_error_file,
        strength=0.85,
        temperature=1.0,
        verbose=True
    )

    # --- Assertions ---
    assert isinstance(result, tuple)
    assert len(result) == 7 # Expect 7 elements

    update_unit_test, update_code, fixed_unit_test, fixed_code, analysis, cost, model = result

    # Verify the function returns the correct fix status and content
    assert update_unit_test is True
    assert update_code is False
    assert fixed_code == ""
    assert "@patch(\"pdd_wrapper.pd.read_csv\")" in fixed_unit_test
    assert "@patch(\"pd.read_csv\")" not in fixed_unit_test # Ensure original incorrect patch is removed
    assert analysis == analysis_result
    assert cost == 0.003
    assert model == "claude-analysis" # Model from first call

    # Check that llm_invoke was called twice
    assert mock_llm_invoke.call_count == 2

    # Optional: Check args of the second call to ensure analysis was passed
    second_call_args = mock_llm_invoke.call_args_list[1]
    kwargs = second_call_args.kwargs
    assert kwargs['input_json']['unit_test_fix'] == analysis_result
    assert kwargs['output_pydantic'] == CodeFix