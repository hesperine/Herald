#!/usr/bin/env python3
"""Cross-platform local launcher that injects local.env into HERALD."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_local_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError("local.env was not found; copy local.env.example first")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise ValueError("local.env must use UTF-8 encoding") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"local.env line {line_number} is invalid")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f"local.env line {line_number} is invalid")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def build_child_environment(values: dict[str, str]) -> dict[str, str]:
    child_environment = os.environ.copy()
    child_environment.update(values)
    source_path = str(PROJECT_ROOT / "src")
    existing_pythonpath = child_environment.get("PYTHONPATH")
    child_environment["PYTHONPATH"] = (
        source_path + os.pathsep + existing_pythonpath
        if existing_pythonpath
        else source_path
    )
    return child_environment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load local.env into a child process and run HERALD locally."
    )
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / "local.env")
    parser.add_argument(
        "--state-dir", type=Path, default=PROJECT_ROOT / ".herald-work/local-state"
    )
    parser.add_argument(
        "--page-dir", type=Path, default=PROJECT_ROOT / ".herald-work/local-page"
    )
    parser.add_argument("--now", help="timezone-aware ISO timestamp")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        values = load_local_env(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"run-local: {exc}", file=sys.stderr)
        return 2

    child_environment = build_child_environment(values)
    command = [
        sys.executable,
        "-m", "herald",
        "--state-dir", str(args.state_dir),
        "--page-dir", str(args.page_dir),
    ]
    if args.now:
        command.extend(["--now", args.now])
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=child_environment,
            check=False,
        )
    except OSError:
        print("run-local: Python could not start HERALD", file=sys.stderr)
        return 2
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
