#!/usr/bin/env python3
"""Flag protected lexical spans changed by a prose edit.

The comparison is read-only and dependency-free. A clean result never proves
semantic equivalence; meaning, attribution, and voice still require review.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


MAX_BYTES = 2 * 1024 * 1024
LIMITATION = "This lexical comparison does not establish semantic equivalence."
FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---(?:\r?\n|\Z)", re.DOTALL)
FENCE_LINE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(?:[^\n]*)$")
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)")
QUOTE_RE = re.compile(r'(?:"([^"\n]{2,})"|“([^”\n]{2,})”)')
CUE_PATTERNS = {
    "negation": re.compile(r"\b(?:no|not|never|neither|nor|without|unless)\b", re.IGNORECASE),
    "uncertainty": re.compile(r"\b(?:may|might|could|possibly|probably|likely|unlikely|uncertain)\b", re.IGNORECASE),
    "attribution": re.compile(r"\b(?:according to|said|says|reported|reports|attributed to)\b", re.IGNORECASE),
}
TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str], int | None], ...] = (
    ("markdown_link_target", re.compile(r"(?<!\!)\[[^\]\n]+\]\(([^\s)]+)(?:\s+['\"][^\n]*?['\"])?\)"), 1),
    ("image_link_target", re.compile(r"!\[[^\]\n]*\]\(([^\s)]+)(?:\s+['\"][^\n]*?['\"])?\)"), 1),
    ("url", re.compile(r"https?://[^\s<>\]\[}`]+"), None),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), None),
    ("doi", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE), None),
    ("citation", re.compile(r"(?:\[\^[^\]\n]+\]|\[@[^\]\n]+\]|\[[0-9]+(?:\s*[-,]\s*[0-9]+)*\]|\\cite\{[^}\n]+\})"), None),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE), None),
    ("sha", re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f])", re.IGNORECASE), None),
    ("numeric", re.compile(r"(?<![\w])(?:[$€£¥]\s*)?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s?(?:%|‰|[kKmMbBtT]))?(?![\w])"), None),
    ("cli_flag", re.compile(r"(?<![\w-])--?[a-zA-Z][a-zA-Z0-9-]*(?![\w-])"), None),
    ("identifier", re.compile(r"\b(?:[A-Za-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Z0-9_-]*\d+[A-Z0-9_-]*)\b"), None),
)


@dataclass(frozen=True)
class Span:
    category: str
    value: str
    start: int
    end: int


class InputError(RuntimeError):
    """The comparison is inconclusive because an input cannot be parsed safely."""


def _read(path: Path) -> str:
    try:
        if not path.is_file():
            raise InputError(f"not a regular file: {path}")
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise InputError(f"file exceeds {MAX_BYTES} bytes: {path}")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc


def _fenced_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    opened: tuple[str, int, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = FENCE_LINE_RE.fullmatch(content)
        if match:
            marker = match.group(1)
            if opened is None:
                opened = (marker, offset, len(marker))
            elif marker[0] == opened[0][0] and len(marker) >= opened[2]:
                end = offset + len(line)
                spans.append(Span("fenced_code", text[opened[1]:end], opened[1], end))
                opened = None
        offset += len(line)
    if opened is not None:
        raise InputError("unterminated fenced code block")
    return spans


def _overlaps(start: int, end: int, claimed: list[tuple[int, int]]) -> bool:
    return any(start < right and end > left for left, right in claimed)


def extract(text: str) -> tuple[dict[str, list[str]], dict[str, Counter[str]]]:
    if text.startswith("---") and FRONTMATTER_RE.match(text) is None:
        raise InputError("unterminated leading YAML frontmatter")

    spans: list[Span] = []
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter:
        spans.append(Span("frontmatter", frontmatter.group(0), *frontmatter.span()))
    spans.extend(_fenced_spans(text))
    claimed = [(item.start, item.end) for item in spans]

    for match in INLINE_CODE_RE.finditer(text):
        start, end = match.span()
        if not _overlaps(start, end, claimed):
            spans.append(Span("inline_code", match.group(2), start, end))
            claimed.append((start, end))

    for match in QUOTE_RE.finditer(text):
        start, end = match.span()
        if not _overlaps(start, end, claimed):
            value = next(value for value in match.groups() if value is not None)
            spans.append(Span("quotation", value, start, end))
            claimed.append((start, end))

    for category, pattern, group in TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span(0 if group is None else group)
            if not _overlaps(start, end, claimed):
                spans.append(Span(category, match.group(0 if group is None else group), start, end))
                claimed.append((start, end))

    ordered: dict[str, list[str]] = {}
    for span in sorted(spans, key=lambda item: (item.start, item.end, item.category)):
        ordered.setdefault(span.category, []).append(span.value)
    cues = {
        name: Counter(match.group(0).lower() for match in pattern.finditer(text))
        for name, pattern in CUE_PATTERNS.items()
    }
    return ordered, cues


def _safe_values(counter: Counter[str], show_values: bool) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for value, count in sorted(counter.items()):
        record: dict[str, Any] = {
            "sha256_prefix": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
            "count": count,
        }
        if show_values:
            record["value"] = value
        values.append(record)
    return values


def compare(source: str, edited: str, *, show_values: bool = False) -> dict[str, Any]:
    before, before_cues = extract(source)
    after, after_cues = extract(edited)
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    categories = sorted(set(before) | set(after))
    for category in categories:
        old_values = before.get(category, [])
        new_values = after.get(category, [])
        old_counter = Counter(old_values)
        new_counter = Counter(new_values)
        if old_counter != new_counter:
            findings.append(
                {
                    "category": category,
                    "missing": _safe_values(old_counter - new_counter, show_values),
                    "added": _safe_values(new_counter - old_counter, show_values),
                }
            )
        elif old_values != new_values:
            warnings.append({"category": category, "kind": "order_changed"})

    for category in sorted(CUE_PATTERNS):
        if before_cues[category] != after_cues[category]:
            warnings.append({"category": category, "kind": "semantic_cue_changed"})

    protected_count = sum(len(values) for values in before.values())
    if protected_count == 0:
        warnings.append({"category": "coverage", "kind": "no_protected_spans_in_source"})
    return {
        "status": "lexical_mismatch" if findings else "no_lexical_mismatch",
        "semantic_equivalence_verified": False,
        "protected_source_occurrences": protected_count,
        "findings": findings,
        "warnings": warnings,
        "limitation": LIMITATION,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flag protected lexical drift; never proves semantic equivalence."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("edited", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--show-values", action="store_true")
    parser.add_argument("--strict-order", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = compare(_read(args.source), _read(args.edited), show_values=args.show_values)
    except InputError as exc:
        result = {
            "status": "inconclusive",
            "semantic_equivalence_verified": False,
            "error": str(exc),
            "limitation": LIMITATION,
        }
        if args.as_json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"Inconclusive: {exc}", file=sys.stderr)
            print(LIMITATION, file=sys.stderr)
        return 2

    strict_order_failure = args.strict_order and any(
        item["kind"] == "order_changed" for item in result["warnings"]
    )
    warning_failure = args.fail_on_warning and bool(result["warnings"])
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result["findings"]:
            for item in result["findings"]:
                print(f"{item['category']}: protected lexical occurrences changed")
        for item in result["warnings"]:
            print(f"warning: {item['category']} ({item['kind']})")
        print(LIMITATION)
    return 1 if result["findings"] or strict_order_failure or warning_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
