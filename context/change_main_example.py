import os
import click
from pathlib import Path
from rich import print as rprint

# Absolute import assuming the module is located in 'my_module_directory'
from pdd.change_main import change_main

def main() -> None:
    """
    Example usage of the change_main function from the 'pdd' command-line program.

    This example demonstrates how to use the 'change' command in both single-change mode
    and CSV batch-change mode.

    Prerequisites:
    - The 'change_main' module is available and can be imported.
    - All required packages are installed.
    - The environment variables are already set.

    The example will:
    - Create sample input files.
    - Call 'change_main' in single-change mode.
    - Call 'change_main' in CSV batch-change mode.
    - Display the outputs.
    """

    # Set up the Click context with necessary parameters and options
    ctx = click.Context(click.Command("change"))

    # Set up CLI options (as would be parsed from command-line arguments)
    # ctx.obj["force"] = True  # Do not overwrite existing files
    # ctx.obj["quiet"] = False  # Verbose output
    # ctx.obj["verbose"] = True  # Non-verbose output

    # Set up global options accessible via 'ctx.obj'
    ctx.obj = {
        "force" : True,  # Do not overwrite existing files
        "quiet" : False,  # Verbose output
        "verbose" : True,  # Non-verbose output
        "strength": .8,      # LLM strength parameter (0.0 to 1.0)
        "temperature": 0,     # LLM temperature parameter (0.0 to 1.0)
        "language": "python", # Programming language for code files
        "extension": ".py",   # File extension for code files
        "budget": 10.0,        # Maximum budget in dollars
    }

    # Create directories for the example
    os.makedirs("output/example_code_directory", exist_ok=True)
    os.makedirs("output/example_output_directory", exist_ok=True)

    # ------------- Single-Change Mode Example -------------

    # Create sample input files for single-change mode
    change_prompt_file = "output/change_prompt.prompt"
    input_code_file = "output/example_code.py"
    input_prompt_file = "output/input_prompt.prompt"
    output_file = "output/modified_prompt.prompt"

    with open(change_prompt_file, "w") as f:
        f.write("Modify the function to add error handling for division by zero.")

    with open(input_code_file, "w") as f:
        f.write("""
def divide(a, b):
    return a / b
""")

    with open(input_prompt_file, "w") as f:
        f.write("Write a function to perform division of two numbers.")
    # base = 'split'
    # change_prompt_file = "context/change/22/change.prompt"
    # input_code_file = f"pdd/{base}.py"
    # input_prompt_file = f"prompts/{base}_python.prompt"
    # output_file = f"/{base}_main_python.prompt"

    # Call change_main in single-change mode
    rprint("[bold underline]Single-Change Mode Example[/bold underline]")
    modified_prompt, total_cost, model_name = change_main(
        ctx=ctx,
        change_prompt_file=change_prompt_file,
        input_code=input_code_file,
        input_prompt_file=input_prompt_file,
        output=output_file,
        use_csv=False,  # CSV mode disabled
    )

    # Display the outputs
    rprint(f"\n[bold]Modified Prompt:[/bold]\n{modified_prompt}")
    rprint(f"[bold]Total Cost:[/bold] ${total_cost:.6f}")
    rprint(f"[bold]Model Used:[/bold] {model_name}")

#     # ------------- CSV Batch-Change Mode Example -------------

    # Create sample code files in a directory
    code_directory = "output/example_code_directory"
    prompt_file_1 = Path(code_directory) / "script1_python.prompt"
    prompt_file_2 = Path(code_directory) / "script2_python.prompt"

    with open(prompt_file_1, "w") as f:
        f.write("Create the function to add two numbers.")
    
    with open(prompt_file_2, "w") as f:
        f.write("Create the function to subtract two numbers.")

    code_file_1 = Path(code_directory) / "script1.py"
    code_file_2 = Path(code_directory) / "script2.py"

    with open(code_file_1, "w") as f:
        f.write("""
def add(a, b):
    return a + b
""")

    with open(code_file_2, "w") as f:
        f.write("""
def subtract(a, b):
    return a - b
""")

    # Create a CSV file specifying changes for batch processing
    csv_change_prompt_file = "output/batch_changes.csv"
    with open(csv_change_prompt_file, "w") as csvfile:
        csvfile.write("prompt_name,change_instructions\n")
        csvfile.write(f"{prompt_file_1},Modify the function to handle overflow errors.\n")
        csvfile.write(f"{prompt_file_2},Optimize the function for large integers.\n")

    # Define output file for batch changes
    batch_output_file = "output" #"output/batch_modified_prompts.csv"

    # Call change_main in CSV batch-change mode
    rprint("\n[bold underline]CSV Batch-Change Mode Example[/bold underline]")
    message, total_cost_csv, model_name_csv = change_main(
        ctx=ctx,
        change_prompt_file=csv_change_prompt_file,
        input_code=code_directory,
        input_prompt_file=None,  # Not used in CSV mode
        output=batch_output_file,
        use_csv=True  # CSV mode enabled
    )

    # Display the outputs
    rprint(f"\n[bold]{message}[/bold]")
    rprint(f"[bold]Total Cost:[/bold] ${total_cost_csv:.6f}")
    rprint(f"[bold]Model Used:[/bold] {model_name_csv}")
    rprint(f"[bold]Batch Results Saved to:[/bold] {batch_output_file}")

if __name__ == "__main__":
    main()
