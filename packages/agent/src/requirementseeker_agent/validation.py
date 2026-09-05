"""Bounded UTF-8 file loading and safe diagnostics for the offline CLI."""

import re
from pathlib import Path
from typing import TypedDict

from pydantic import ValidationError

MAX_FILE_BYTES = 2 * 1024 * 1024


class Issue(TypedDict):
    location: list[str | int]
    code: str


class InputFileError(Exception):
    """Contains only a fixed diagnostic code, never file contents."""


def read_json_text(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise InputFileError("file_unreadable") from None
    if len(raw) > MAX_FILE_BYTES:
        raise InputFileError("file_too_large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError:
        raise InputFileError("file_not_utf8") from None


def issues(error: ValidationError) -> list[Issue]:
    result: list[Issue] = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        code = item["type"]
        if code == "value_error":
            rule = item["msg"].removeprefix("Value error, ")
            if re.fullmatch(r"[a-z][a-z0-9_]+", rule):
                code = rule
        location = list(item["loc"])
        if item["type"] == "extra_forbidden" and location:
            location[-1] = "<unknown-field>"
        result.append({"location": location, "code": code})
    return result
