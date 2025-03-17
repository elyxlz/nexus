import asyncio
import pathlib as pl

from nexus.server.core import job, logger


async def test_launch_screen():
    test_logger = logger.NexusServerLogger("test")

    # Create a simple script to run in screen
    script_path = pl.Path("/tmp/test_screen_script.sh")
    script_content = """#!/bin/bash
    echo "Hello from screen process"
    sleep 2
    echo "Exiting screen process"
    """
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    session_name = "test_screen_session"
    env = {"TEST_VAR": "test_value"}

    print(f"Testing _launch_screen_process with session '{session_name}' and script '{script_path}'")
    try:
        pid = await job._launch_screen_process(test_logger, session_name, str(script_path), env)
        print(f"Successfully launched screen process with PID: {pid}")

        # Check if the screen session exists
        proc = await asyncio.create_subprocess_exec("screen", "-ls", stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        screen_output = stdout.decode()

        if session_name in screen_output:
            print(f"Screen session '{session_name}' is running")
        else:
            print(f"Warning: Could not find screen session '{session_name}' in screen -ls output")

        # Wait for the script to complete
        print("Waiting for process to complete...")
        await asyncio.sleep(3)

        # Check if process still exists
        try:
            import os

            os.kill(pid, 0)
            print(f"Process {pid} is still running")
        except ProcessLookupError:
            print(f"Process {pid} has completed")

        # Clean up the screen session
        proc = await asyncio.create_subprocess_exec(
            "screen", "-S", session_name, "-X", "quit", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
        print(f"Cleaned up screen session '{session_name}'")

    except Exception as e:
        print(f"Error testing _launch_screen_process: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Clean up the test script
        script_path.unlink(missing_ok=True)
        print(f"Removed test script '{script_path}'")


if __name__ == "__main__":
    asyncio.run(test_launch_screen())
