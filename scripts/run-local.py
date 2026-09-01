#!/usr/bin/env python3
"""Cross-platform launcher for private local environment and AI profiles."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AI_PROFILE_FIELDS = {
    "AI_PROVIDER": str,
    "AI_BASE_URL": str,
    "AI_MODEL": str,
    "AI_VISION": bool,
    "AI_JSON_MODE": bool,
    "AI_API_KEY": str,
}


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


def load_ai_profile(path: Path, profile_name: str) -> dict[str, str]:
    """Load one private local AI profile without exposing its values."""

    if not path.is_file():
        raise ValueError(
            "local AI provider file was not found; "
            "copy local-ai-providers.toml.example first"
        )
    try:
        with path.open("rb") as provider_file:
            profiles: dict[str, Any] = tomllib.load(provider_file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("local AI provider file is invalid TOML") from exc

    profile = profiles.get(profile_name)
    if profile is None:
        raise ValueError(
            f"AI_PROFILE {profile_name!r} was not found in local AI provider file"
        )
    if not isinstance(profile, dict):
        raise ValueError(f"AI_PROFILE {profile_name!r} must be a TOML table")

    unknown_fields = sorted(set(profile) - set(AI_PROFILE_FIELDS))
    if unknown_fields:
        raise ValueError(
            f"AI_PROFILE {profile_name!r} has unsupported fields: "
            + ", ".join(unknown_fields)
        )
    missing_fields = sorted(set(AI_PROFILE_FIELDS) - set(profile))
    if missing_fields:
        raise ValueError(
            f"AI_PROFILE {profile_name!r} is missing fields: "
            + ", ".join(missing_fields)
        )

    values: dict[str, str] = {}
    for field, expected_type in AI_PROFILE_FIELDS.items():
        value = profile[field]
        if type(value) is not expected_type:
            expected_name = "boolean" if expected_type is bool else "string"
            raise ValueError(
                f"AI_PROFILE {profile_name!r} field {field} must be a TOML "
                f"{expected_name}"
            )
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError(
                    f"AI_PROFILE {profile_name!r} field {field} must not be empty"
                )
        values[field] = str(value).lower() if isinstance(value, bool) else value
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
        description=(
            "Load local.env and an optional private AI profile into a child "
            "process and run HERALD locally."
        )
    )
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / "local.env")
    parser.add_argument(
        "--provider-file",
        type=Path,
        default=PROJECT_ROOT / "local-ai-providers.toml",
        help="private local AI profiles TOML",
    )
    parser.add_argument(
        "--state-dir", type=Path, default=PROJECT_ROOT / ".herald-work/local-state"
    )
    parser.add_argument(
        "--page-dir", type=Path, default=PROJECT_ROOT / ".herald-work/local-page"
    )
    parser.add_argument("--now", help="timezone-aware ISO timestamp")
    parser.add_argument(
        "--ai-smoke-test",
        action="store_true",
        help="run only one real AI extraction against a fixed public sample",
    )
    parser.add_argument(
        "--collect-weibo-samples",
        action="store_true",
        help="collect bounded public Weibo history without running AI",
    )
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--sample-start-date")
    parser.add_argument("--sample-end-date")
    parser.add_argument("--sample-max-pages", type=int, default=20)
    parser.add_argument(
        "--sample-output",
        type=Path,
        default=PROJECT_ROOT / ".herald-work/ai-sample-candidates.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.collect_weibo_samples and bool(args.sample_start_date) != bool(
        args.sample_end_date
    ):
        print(
            "run-local: --sample-start-date and --sample-end-date "
            "must be provided together",
            file=sys.stderr,
        )
        return 2
    try:
        values = load_local_env(args.env_file)
        profile_name = values.pop("AI_PROFILE", "").strip()
        if profile_name and not args.collect_weibo_samples:
            values.update(load_ai_profile(args.provider_file, profile_name))
    except (OSError, ValueError) as exc:
        print(f"run-local: {exc}", file=sys.stderr)
        return 2

    if args.collect_weibo_samples:
        cookie = values.get("WEIBO_COOKIE", "")
        values = {"WEIBO_COOKIE": cookie} if cookie else {}
    child_environment = build_child_environment(values)
    if args.collect_weibo_samples:
        command = [
            sys.executable,
            "-m",
            "herald.weibo_sample_collector",
        ]
        if args.sample_start_date:
            command.extend(
                [
                    "--start-date",
                    args.sample_start_date,
                    "--end-date",
                    args.sample_end_date,
                ]
            )
        else:
            command.extend(["--lookback-days", str(args.lookback_days)])
        command.extend(
            [
                "--max-pages",
                str(args.sample_max_pages),
                "--output",
                str(args.sample_output),
            ]
        )
    elif args.ai_smoke_test:
        command = [sys.executable, "-m", "herald.ai_smoke"]
    else:
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
