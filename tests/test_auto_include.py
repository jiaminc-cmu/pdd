"""Unit tests for the auto_include module."""
from unittest.mock import patch, MagicMock
import pytest
from pdd.auto_include import auto_include


@pytest.fixture(name="mock_load_prompt_template")
def mock_load_prompt_template_fixture():
    """Fixture to mock load_prompt_template calls."""
    with patch("pdd.auto_include.load_prompt_template") as mock_load:
        yield mock_load


@pytest.fixture(name="mock_summarize_directory")
def mock_summarize_directory_fixture():
    """Fixture to mock summarize_directory calls."""
    with patch("pdd.auto_include.summarize_directory") as mock_summarize:
        yield mock_summarize


@pytest.fixture(name="mock_llm_invoke")
def mock_llm_invoke_fixture():
    """Fixture to mock llm_invoke calls."""
    with patch("pdd.auto_include.llm_invoke") as mock_llm:
        yield mock_llm


def test_auto_include_valid_call(
    mock_load_prompt_template, mock_summarize_directory, mock_llm_invoke
):
    """
    Test a successful call to auto_include with valid parameters.
    Ensures we get back a tuple of (dependencies, csv_output, total_cost, model_name).
    """
    # Mock prompt templates
    mock_load_prompt_template.side_effect = lambda name: f"{name} content"

    # Mock summarize_directory return
    mock_summarize_directory.return_value = (
        "full_path,file_summary,date\n"
        "context/example.py,Example summary,2023-02-02",
        0.25,
        "mock-summary-model",
    )

    # Mock llm_invoke for auto_include_LLM step
    mock_llm_invoke.side_effect = [
        {
            "result": "Mocked auto_include_LLM output",
            "cost": 0.5,
            "model_name": "mock-model-1",
        },
        # Mock llm_invoke for extract_auto_include_LLM step (pydantic)
        {
            "result": MagicMock(string_of_includes="from .context.example import Example"),
            "cost": 0.75,
            "model_name": "mock-model-2",
        },
    ]

    deps, csv_out, total_cost, model_name = auto_include(
        input_prompt="Process image data",
        directory_path="context/*.py",
        csv_file=None,
        strength=0.7,
        temperature=0.0,
        verbose=False,
    )

    assert "from .context.example import Example" in deps
    assert "full_path,file_summary,date" in csv_out
    # total_cost should be sum of summarize_directory (0.25) +
    # first llm_invoke (0.5) + second llm_invoke (0.75) = 1.5
    assert total_cost == 1.5
    # The last model used is from the second llm_invoke
    assert model_name == "mock-model-2"


def test_auto_include_fail_load_templates(mock_load_prompt_template):
    """
    Test that a failure to load either prompt template raises a ValueError.
    """
    # Return None for one of the templates
    mock_load_prompt_template.side_effect = [None, "extract_auto_include_LLM content"]

    with pytest.raises(ValueError) as excinfo:
        auto_include(
            input_prompt="Valid prompt",
            directory_path="context/*.py",
            csv_file=None,
            strength=0.7,
            temperature=0.0,
            verbose=False,
        )
    assert "Failed to load prompt templates" in str(excinfo.value)


def test_auto_include_csv_parsing_error(
    mock_load_prompt_template, mock_summarize_directory, mock_llm_invoke
):
    """
    Test that an invalid CSV does not raise but logs an error and sets available_includes = [].
    We verify it proceeds to subsequent steps and handles it gracefully.
    """
    # Mock prompt templates
    mock_load_prompt_template.side_effect = lambda name: f"{name} content"

    # Mock summarize_directory to return an invalid CSV
    mock_summarize_directory.return_value = (
        "not_a_valid_csv",
        0.25,
        "mock-summary-model",
    )

    # Mock llm_invoke
    mock_llm_invoke.side_effect = [
        {
            "result": "Mocked auto_include_LLM output",
            "cost": 0.5,
            "model_name": "mock-model-1",
        },
        {
            "result": MagicMock(string_of_includes="from .some_import import SomeClass"),
            "cost": 0.75,
            "model_name": "mock-model-2",
        },
    ]

    deps, _, total_cost, model_name = auto_include(
        input_prompt="Valid prompt",
        directory_path="context/*.py",
        csv_file=None,
        strength=0.7,
        temperature=0.0,
        verbose=False,
    )

    # Dependencies are extracted from the second mock
    assert "from .some_import import SomeClass" in deps
    assert total_cost == 1.5
    assert model_name == "mock-model-2"


def test_auto_include_llm_invoke_error_extract(
    mock_load_prompt_template, mock_summarize_directory, mock_llm_invoke
):
    """
    Test that if the second llm_invoke (extract_auto_include_LLM) fails,
    dependencies are set to "".
    """
    # Mock templates
    mock_load_prompt_template.side_effect = lambda name: f"{name} content"

    # Mock summarize_directory
    mock_summarize_directory.return_value = (
        "full_path,file_summary,date\n"
        "context/example.py,Example summary,2023-02-02",
        0.2,
        "mock-summary-model",
    )

    # First llm_invoke works, second fails
    mock_llm_invoke.side_effect = [
        {
            "result": "Mocked auto_include_LLM output",
            "cost": 0.3,
            "model_name": "mock-model-1",
        },
        # Raise exception on second call
        Exception("Test extraction failure"),
    ]

    deps, _, total_cost, model_name = auto_include(
        input_prompt="Valid prompt",
        directory_path="context/*.py",
        csv_file=None,
        strength=0.7,
        temperature=0.0,
        verbose=False,
    )

    # The second step fails, so dependencies remain ""
    assert deps == ""
    # only the cost of summarize_directory + first llm_invoke
    assert total_cost == 0.5
    # The last successful model name is "mock-model-1"
    assert model_name == "mock-model-1"