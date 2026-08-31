"""Extract the staff roster from a Cert Form PDF into staff.json.

The form is an Excel export whose fonts are subset with glyph-id encoding, so the
text has to be decoded through the embedded ToUnicode CMaps. Names sit in the
left-hand column; each row's remaining text is one run ending in the six-character
LS number. Everyone below the "Away Spring / Summer" marker is flagged away.

Usage: python scripts/extract_roster.py "Cert Form June 2024.pdf" staff.json
"""

from __future__ import annotations

import json
import re
import sys
import zlib
from pathlib import Path

NAME_COLUMN_X = 44.0
AWAY_MARKER = "Away Spring / Summer"
LS_NUMBER = re.compile(r"([A-Z0-9]{6})$")


def _cmaps(data: bytes) -> list[dict[int, str]]:
    maps = []
    for block in re.finditer(rb"begincmap(.*?)endcmap", data, re.S):
        mapping: dict[int, str] = {}
        for chunk in re.finditer(rb"beginbfchar(.*?)endbfchar", block.group(1), re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk.group(1)):
                mapping[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be", "replace")
        for chunk in re.finditer(rb"beginbfrange(.*?)endbfrange", block.group(1), re.S):
            for lo, hi, start in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk.group(1)
            ):
                low, high, first = int(lo, 16), int(hi, 16), int(start, 16)
                for code in range(low, high + 1):
                    mapping[code] = chr(first + code - low)
        maps.append(mapping)
    return maps


def _text_runs(data: bytes) -> list[tuple[int, float, str]]:
    maps = _cmaps(data)
    fonts = {f"F{index + 1}": mapping for index, mapping in enumerate(maps)}
    content = None
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            candidate = zlib.decompress(stream)
        except zlib.error:
            continue
        # Embedded TTF binaries also happen to contain "BT"/"Tm", so require the
        # stream to be ASCII page description text rather than a font blob.
        if b"] TJ" in candidate and candidate[:200].isascii():
            content = candidate.decode("latin-1")
            break
    if content is None:
        raise SystemExit("No text content stream found in the PDF.")

    runs: list[tuple[int, float, str]] = []
    font = "F1"
    x = y = 0.0
    pattern = r"/(F\d) [\d.]+ Tf|1 0 0\.000000 -1 ([-\d.]+) ([-\d.]+) Tm|\[(.*?)\] TJ"
    for match in re.finditer(pattern, content):
        if match.group(1):
            font = match.group(1)
        elif match.group(2):
            x, y = float(match.group(2)), float(match.group(3))
        else:
            mapping = fonts.get(font, {})
            text = "".join(
                mapping.get(int(code, 16), "?")
                for code in re.findall(r"<([0-9A-Fa-f]+)>", match.group(4))
            )
            runs.append((round(y), x, text))
    return runs


def extract(pdf_path: Path) -> list[dict[str, object]]:
    runs = _text_runs(pdf_path.read_bytes())
    names: dict[int, str] = {}
    details: dict[int, str] = {}
    away_from: int | None = None

    for y, x, text in runs:
        if AWAY_MARKER.replace(" ", "") in text.replace(" ", ""):
            away_from = y
        elif abs(x - NAME_COLUMN_X) < 0.5:
            names[y] = text.strip()
        elif x > 180:
            details[y] = text

    roster: list[dict[str, object]] = []
    seen: set[str] = set()
    for y in sorted(set(names) | set(details)):
        name = names.get(y) or names.get(y - 1) or names.get(y + 1)
        detail = details.get(y) or details.get(y + 1) or details.get(y - 1)
        if not name or not detail or name in seen or name.endswith(":"):
            continue
        match = LS_NUMBER.search(detail)
        if not match:
            print(f"warning: no LS# found for {name!r}", file=sys.stderr)
            continue
        seen.add(name)
        entry: dict[str, object] = {"name": name, "memberCode": match.group(1)}
        if away_from is not None and y > away_from:
            entry["away"] = True
        roster.append(entry)
    return roster


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    roster = extract(Path(argv[1]))
    Path(argv[2]).write_text(json.dumps(roster, indent=2) + "\n", encoding="utf-8")
    away = sum(1 for entry in roster if entry.get("away"))
    print(f"Wrote {len(roster)} staff ({away} away) to {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
