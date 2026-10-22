"""Tiny ANSI styling helpers (replaces the `colorize` npm package)."""

import re

RESET = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def bold(text: str) -> str:
    return f"\033[1m{text}{RESET}"


def red(text: str) -> str:
    return f"\033[31m{text}{RESET}"


def green(text: str) -> str:
    return f"\033[32m{text}{RESET}"


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)
