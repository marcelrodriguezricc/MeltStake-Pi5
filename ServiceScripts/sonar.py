#!/home/pi/meltstake/venv/bin/python
# pyright: reportMissingImports=false

"""
Melt Stake Sonar System Controller - systemd service startup script.

Automatic entry point meltstake_sonar as a
systemd service on startup. Activates the project virtual environment, builds
the command with the desired arguments, and exec's into the module.
"""

import os
import sys
import signal
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_DIR = Path.home() / "meltstake-sonar"
VENV_DIR = PROJECT_DIR / "venv"
CONFIG_FILE = "default_config.toml"
DATA_DIR = "/mnt/nvme/data"
# ---------------------------------------------------------------------------


def get_venv_python(venv_dir: Path) -> Path:
    """Return the path to the virtual environment's Python interpreter."""
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        print(
            f"[start_service] ERROR: Virtual environment Python not found at {venv_python}",
            file=sys.stderr,
        )
        sys.exit(1)
    return venv_python


def build_command(venv_python: Path) -> list[str]:
    """Build the command list for running the meltstake_sonar module."""
    cmd = [
        str(venv_python),
        "-m", "meltstake_sonar",
        "--config", CONFIG_FILE,
        "--data", str(DATA_DIR),
    ]
    return cmd


def forward_signal(proc: subprocess.Popen, signum: int, _frame) -> None:
    """Forward termination signals to the child process for graceful shutdown."""
    proc.send_signal(signum)


def main() -> None:

    # Change working directory to repository main directory
    os.chdir(PROJECT_DIR)

    # Get path to venv
    venv_python = get_venv_python(VENV_DIR)

    # Build command to run the program
    cmd = build_command(venv_python)

    print(f"[start_service] Working directory: {PROJECT_DIR}")
    print(f"[start_service] Running: {' '.join(cmd)}")

    # Run software
    proc = subprocess.Popen(cmd)

    # Forward SIGTERM and SIGINT to the child so systemd stop is graceful.
    signal.signal(signal.SIGTERM, lambda s, f: forward_signal(proc, s, f))
    signal.signal(signal.SIGINT, lambda s, f: forward_signal(proc, s, f))

    # Wait until process finishes before exiting
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()