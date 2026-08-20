"""The thin finder adapter retains every documented migration flag."""

import subprocess
import sys


def test_finder_help_retains_milestone_4_compatibility_flags():
    result = subprocess.run(
        [sys.executable, "-m", "jaws.jaws_finder", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for flag in (
        "--components",
        "--whiten",
        "--eps",
        "--feature-weight",
        "--include-local",
        "--session",
        "--no-baseline",
        "--ablate",
    ):
        assert flag in result.stdout
