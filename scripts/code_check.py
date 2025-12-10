#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

SERVICES = ["services/orchestrator", "services/gpu-ffmpeg", "services/folder-watcher"]


def check_venv():
    """Ensure we are running in a virtual environment."""
    if sys.prefix == sys.base_prefix:
        print(f"{RED}❌ ERROR: strictly_venv_enforced.{RESET}")
        print("This script must be run within the active virtual environment.")
        print("👉 Please run: source .venv/bin/activate")
        sys.exit(1)


def run_command(command, cwd=None, env=None, description=None):
    """Run a command and return its exit code."""
    if description:
        print(f"\n{BLUE}=== {description} ==={RESET}")
    else:
        print(f"Running: {' '.join(command)}")

    try:
        # Use current env if not provided, else merge
        run_env = os.environ.copy()
        # Ensure venv bin is in PATH
        venv_bin = os.path.join(sys.prefix, "bin")
        run_env["PATH"] = venv_bin + os.pathsep + run_env.get("PATH", "")

        if env:
            run_env.update(env)

        result = subprocess.run(command, cwd=cwd, env=run_env, check=False)
        if result.returncode != 0:
            print(f"{RED}>>> FAILED: {' '.join(command)} (Exit code: {result.returncode}){RESET}")
        else:
            print(f"{GREEN}>>> PASSED{RESET}")
        return result.returncode
    except FileNotFoundError:
        print(f"{RED}Error: Command '{command[0]}' not found.{RESET}")
        return 127


def main():
    check_venv()
    project_root = Path(__file__).parent.parent.resolve()
    os.chdir(project_root)

    failures = []

    # 1. Global Linters (Fast, file-based)
    print(f"\n{BLUE}📊 Running Global Linters...{RESET}")
    if run_command(["ruff", "check", "."], description="Global Ruff") != 0:
        failures.append("Global Ruff")
    if run_command(["black", "--check", "."], description="Global Black") != 0:
        failures.append("Global Black")

    # 2. Per-Service Type Checking & Tests (Isolated)
    for service_rel_path in SERVICES:
        service_path = project_root / service_rel_path
        if not service_path.exists():
            print(f"{RED}Warning: Service {service_rel_path} not found, skipping.{RESET}")
            continue

        service_name = service_rel_path.split("/")[-1]
        print(f"\n{BLUE}🏗️  Processing Service: {service_name}{RESET}")

        # Set PYTHONPATH to this service only to resolve 'app' correctly
        service_env = {"PYTHONPATH": str(service_path)}

        # Mypy
        # Run inside the service dir to capture local config if any, or point to it
        if (
            run_command(
                ["mypy", "."],
                cwd=service_path,
                env=service_env,
                description=f"Mypy ({service_name})",
            )
            != 0
        ):
            failures.append(f"Mypy: {service_name}")

        # Pytest
        if (
            run_command(
                ["pytest"],
                cwd=service_path,
                env=service_env,
                description=f"Pytest ({service_name})",
            )
            != 0
        ):
            failures.append(f"Pytest: {service_name}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("-" * 60)

    if failures:
        print(f"{RED}Some checks failed:{RESET}")
        for f in failures:
            print(f" - {f}")
        sys.exit(1)
    else:
        print(f"{GREEN}All checks passed!{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
