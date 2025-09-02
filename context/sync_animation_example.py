"""
Example of how to use the sync_animation module.

This script demonstrates:
1. Setting up shared state variables (mutable lists and a threading.Event)
   that the main application (simulated here) can update.
2. Starting the `sync_animation` in a separate thread, passing these shared variables.
3. Simulating a main application workflow that changes the shared state,
   which in turn updates the animation.
4. Signaling the animation to stop and cleaning up the thread.
"""
import threading
import time
import os

# Assuming the sync_animation module is in a 'pdd' directory
# relative to this example script, and the file is named 'sync_animation.py'.
from pdd.sync_animation import sync_animation

# --- 1. Define Shared State Variables ---
# These variables are updated by the main application logic and read by the
# animation thread. They must be mutable (e.g., lists of length 1).

# current_function_name_ref: List[str]
#   - Holds the name of the PDD command currently being simulated (e.g., "generate", "test").
#   - The animation displays this name.
current_function_name_ref = ["checking"]

# stop_event: threading.Event
#   - Used to signal the animation thread that it should terminate gracefully.
stop_event = threading.Event()

# current_cost_ref: List[float]
#   - Holds the accumulated cost of operations, typically in dollars.
#   - The animation displays this cost.
current_cost_ref = [0.0]

# Path references: List[str] for each
#   - Hold strings representing paths to relevant files (prompt, code, example, tests).
#   - The animation displays these paths, shortening them if they are too long.
#   - For this example, paths point to a hypothetical './output/' directory.
initial_prompt_path = "./"
prompt_path_ref = [initial_prompt_path + "prompts/handpaint_html.prompt"]
code_path_ref = [initial_prompt_path + "src/handpaint.html"]
example_path_ref = [initial_prompt_path + "context/handpaint_example.html"]
tests_path_ref = [initial_prompt_path + "tests/tests_handpaint.html"]

# --- 2. Define Static Parameters for the Animation ---
# These are set once when the animation starts.

# basename: str
#   - The base name of the project or prompt being processed (e.g., "calculator_app").
basename = "handpaint"

# budget_val: Optional[float]
#   - The total budget for the operation, typically in dollars.
#   - Set to None or float('inf') for no budget.
budget_val = 3.00

# Box colors: str (now as mutable refs for dynamic updates)
prompt_box_color_ref = ["blue"]
code_box_color_ref = ["cyan"]
example_box_color_ref = ["green"]
tests_box_color_ref = ["yellow"]


# --- 3. Mock Main Application Workflow ---
# This function simulates a PDD workflow, updating the shared state
# variables at different stages to demonstrate the animation's responsiveness.
def mock_pdd_main_workflow():
    """
    Simulates a PDD workflow, updating shared state for the animation.
    """
    def update_animation_state(func_name, cost_increase, p_path=None, c_path=None, e_path=None, t_path=None,
                              prompt_color=None, code_color=None, example_color=None, tests_color=None):
        """Helper to update shared state and print a log from the main app."""
        print(f"Main App Log: Transitioning to '{func_name}', cost increment: ${cost_increase:.2f}")
        current_function_name_ref[0] = func_name
        current_cost_ref[0] += cost_increase
        if p_path is not None: prompt_path_ref[0] = p_path
        if c_path is not None: code_path_ref[0] = c_path
        if e_path is not None: example_path_ref[0] = e_path
        if t_path is not None: tests_path_ref[0] = t_path
        if prompt_color is not None: prompt_box_color_ref[0] = prompt_color
        if code_color is not None: code_box_color_ref[0] = code_color
        if example_color is not None: example_box_color_ref[0] = example_color
        if tests_color is not None: tests_box_color_ref[0] = tests_color
        # A small delay can sometimes help ensure the animation thread
        # picks up rapid state changes, though Rich's Live is generally efficient.
        time.sleep(0.05)

    try:
        # Initial "checking" state
        update_animation_state("checking", 0.0, prompt_color="red")
        time.sleep(10) # Show "checking" for 2 seconds

        # Simulate 'auto-deps' command
        update_animation_state("auto-deps", 0.024)
        time.sleep(7)

        # Simulate 'generate' command
        update_animation_state("generate", .03135)
        time.sleep(8)

        # Simulate 'example' command
        update_animation_state("example", 0.023)
        time.sleep(6)
        
        # Simulate 'crash' command
        # Note: The animation for 'crash' involves bi-directional arrows.
        update_animation_state("crash", 0.02546)
        time.sleep(12)

        # Simulate 'verify' command
        update_animation_state("verify", 0.03432)
        time.sleep(13)

        # Simulate 'test' command
        update_animation_state("test", 0.0512)
        time.sleep(15)

        # Simulate 'fix' command
        # Note: The animation for 'fix' involves bi-directional arrows.
        update_animation_state("fix", 0.0234)
        time.sleep(25)
        
        # Simulate 'update' command (updating the prompt)
        update_animation_state("update", 0.0132)
        time.sleep(5)

        print("Main App Log: Workflow simulation complete.")

    except KeyboardInterrupt:
        print("Main App Log: Workflow interrupted by user (Ctrl+C).")
    finally:
        # --- 5. Signal Animation to Stop ---
        print("Main App Log: Signaling animation thread to stop.")
        stop_event.set()

if __name__ == "__main__":
    print("Starting PDD Sync...")
    # Record the start time
    start_time = time.time()
    # print("The animation will run in the terminal. Press Ctrl+C to stop the simulation early.")
    # print("Ensure your terminal window is wide enough (at least 80 columns) for best results.")
    # time.sleep(1) # Give user a moment to read

    # Create dummy ./output directory if it doesn't exist, for path shortening tests
    # The _shorten_path function in the module can handle non-existent paths,
    # but creating them makes the relative path logic more robustly testable.
    # os.makedirs("./output/project_alpha/src", exist_ok=True)
    # os.makedirs("./output/project_alpha/examples", exist_ok=True)
    # os.makedirs("./output/project_alpha/tests", exist_ok=True)


    # --- 4. Start the Animation in a Separate Thread ---
    # The `sync_animation` function is designed to be run in its own thread
    # so it doesn't block the main application logic.
    animation_thread = threading.Thread(
        target=sync_animation,
        args=(
            current_function_name_ref,
            stop_event,
            basename,
            current_cost_ref,
            budget_val,
            prompt_box_color_ref,
            code_box_color_ref,
            example_box_color_ref,
            tests_box_color_ref,
            prompt_path_ref,
            code_path_ref,
            example_path_ref,
            tests_path_ref
        ),
        daemon=True # Set as daemon so it exits when the main program exits
    )
    animation_thread.start()

    # Run the mock PDD workflow in the main thread
    mock_pdd_main_workflow()

    # --- 6. Wait for Animation Thread to Clean Up and Exit ---
    # It's good practice to join threads to ensure they complete their cleanup.
    print("Main App Log: Waiting for animation thread to finish...")
    animation_thread.join(timeout=10) # Wait for up to 10 seconds

    if animation_thread.is_alive():
        print("Main App Log: Animation thread did not stop in the allocated time.")

    # Calculate and print total elapsed time and cost
    elapsed_time = time.time() - start_time
    minutes = int(elapsed_time // 60)
    seconds = int(elapsed_time % 60)
    print(f"Total elapsed time: {minutes}m {seconds}s")
    print(f"Total cost: ${current_cost_ref[0]:.4f}")

    print("PDD Sync Finished.")
    # The animation uses Rich's Live(screen=True), which should restore the terminal.
    # If not, a manual clear might be needed (e.g., 'clear' on Linux/macOS, 'cls' on Windows).