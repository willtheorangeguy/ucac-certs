from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .models import StaffMember

MEMBER_CODE_RE = re.compile(r"^[A-Za-z0-9]+$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigurationError(ValueError):
    pass


def load_env_file(path: Path) -> None:
    """Load a small dotenv-style file without overriding existing environment values."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ConfigurationError(f"Unable to read environment file: {exc}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Environment file line {line_number} must contain '='.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise ConfigurationError(f"Environment file line {line_number} has an invalid key.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_staff_json(raw: str) -> list[StaffMember]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Staff JSON is invalid: {exc.msg}") from exc

    if not isinstance(value, list) or not value:
        raise ConfigurationError("Staff JSON must be a non-empty array.")

    members: list[StaffMember] = []
    seen_names: set[str] = set()
    seen_codes: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"Staff entry {index} must be an object.")
        name = item.get("name")
        member_code = item.get("memberCode")
        away = item.get("away", False)
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"Staff entry {index} has an invalid name.")
        if not isinstance(member_code, str) or not MEMBER_CODE_RE.fullmatch(member_code.strip()):
            raise ConfigurationError(f"Staff entry {index} has an invalid memberCode.")
        if not isinstance(away, bool):
            raise ConfigurationError(f"Staff entry {index} has an invalid away flag.")
        name = " ".join(name.split())
        member_code = member_code.strip().upper()
        normalized_name = name.casefold()
        if normalized_name in seen_names:
            raise ConfigurationError(f"Staff entry {index} duplicates a name.")
        if member_code in seen_codes:
            raise ConfigurationError(f"Staff entry {index} duplicates a memberCode.")
        seen_names.add(normalized_name)
        seen_codes.add(member_code)
        members.append(StaffMember(name=name, member_code=member_code, away=away))
    return members


def load_staff_file(path: Path) -> list[StaffMember]:
    try:
        return load_staff_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read staff file: {exc}") from exc


def load_recipients(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("REPORT_RECIPIENTS must be a JSON array.") from exc
    if not isinstance(value, list) or not value:
        raise ConfigurationError("REPORT_RECIPIENTS must be a non-empty JSON array.")
    recipients: list[str] = []
    for item in value:
        if not isinstance(item, str) or "@" not in item or "\n" in item or "\r" in item:
            raise ConfigurationError("REPORT_RECIPIENTS contains an invalid address.")
        recipients.append(item.strip())
    return recipients
