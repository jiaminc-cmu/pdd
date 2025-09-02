from pdd.fix_code_loop import fix_code_loop
import os
from pathlib import Path

def main() -> None:
    """
    Main function to demonstrate the use of fix_code_loop.
    It creates example files, runs the fix_code_loop, and cleans up.
    """
    # Create example files needed for testing
    
    # 1. Create a code file with an error
    code_content = """
def calculate_average(numbers):
    return sum(numbers) / len(numbers)
    """
    code_file = "output/module_to_test.py"
    Path(code_file).write_text(code_content)
    
    # 2. Create a verification program that tests the code
    verify_content = """
from module_to_test import calculate_average

# This will cause an error because we're passing a string
result = calculate_average("123")
print(f"Average is: {result}")
    """
    verification_file = "output/verify_code.py"
    Path(verification_file).write_text(verify_content)
    
    # 3. Define the original prompt that generated the code
    prompt = "Write a function that calculates the average of a list of numbers"

    # module_name = "get_extension"
    # code_file = "pdd/" + module_name + ".py"
    # verification_file = "context/" + module_name + "_example.py"
    # # read prompt from file
    # prompt = Path("prompts/" + module_name + "_python.prompt").read_text()

    
    # Run the fix_code_loop
    success, final_program, final_code, attempts, cost, model = fix_code_loop(
        code_file=code_file,
        prompt=prompt,
        verification_program=verification_file,
        strength=.5,        # Use a moderately strong model
        temperature=0,     # Use deterministic output
        max_attempts=5,      # Try up to 5 fixes
        budget=5,         # Maximum budget of $5 USD
        error_log_file="output/fix_attempt.log",
        verbose=True
    )
    
    # Print results
    print(f"\nFix attempt {'succeeded' if success else 'failed'}")
    print(f"Total attempts: {attempts}")
    print(f"Total cost: ${cost:.4f}")
    print(f"Model used: {model}")
    print("\nFinal code:")
    print(final_code)
    
    # Clean up example files
    # for file in [code_file, verification_file, "fix_attempt.log"]:
    #     if os.path.exists(file):
    #         os.remove(file)

if __name__ == "__main__":
    main()
