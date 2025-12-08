#!/usr/bin/env python3
"""
Single Source of Truth for running quality checks.
"""
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Command:
    name: str
    args: list[str]
    cwd: Path
    extra_env: dict | None = None
    allowed_return_codes: tuple[int, ...] = (0,)


def get_commands() -> list[Command]:
    return [
        Command(name="Ruff", args=["ruff", "check", "--fix", "."], cwd=REPO_ROOT),
        Command(name="Black", args=[sys.executable, "-m", "black", "."], cwd=REPO_ROOT),
        Command(
            name="Orchestrator Tests",
            args=["pytest", "services/orchestrator/tests"],
            cwd=REPO_ROOT,
            extra_env={"PYTHONPATH": str(REPO_ROOT / "services" / "orchestrator")},
        ),
        Command(
            name="GPU Worker Tests",
            args=["pytest", "services/gpu-ffmpeg/tests", "services/gpu-ffmpeg/test_worker.py"],
            cwd=REPO_ROOT,
            extra_env={"PYTHONPATH": str(REPO_ROOT / "services" / "gpu-ffmpeg")},
        ),
    ]


def run_command(command: Command) -> None:
    print(f"\n==> {command.name}")
    sys.stdout.flush()
    env = os.environ.copy()
    if command.extra_env:
        env.update(command.extra_env)

    try:
        result = subprocess.run(command.args, cwd=command.cwd, env=env, check=False, text=True)
        if result.returncode not in command.allowed_return_codes:
            print(f"\n❌ {command.name} failed.")
            sys.exit(result.returncode)
        print(f"✅ {command.name} passed.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    for command in get_commands():
        run_command(command)
    print("\n🎉 All checks passed.")


if __name__ == "__main__":
    main()
