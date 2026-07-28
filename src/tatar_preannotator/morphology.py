from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Protocol


APERTIUM_TAT_REVISION = "18fe9e45d5672d6f6113291197449e7522df1b3e"
DEFAULT_APERTIUM_TAT_DIR = (
    Path(__file__).resolve().parents[2] / ".tools" / "apertium-tat"
)


class MorphologyError(ValueError):
    """Raised when the pinned Tatar morphological analyzer cannot be used."""


@dataclass(frozen=True, order=True)
class MorphIdentity:
    lemma: str
    part_of_speech: str


class MorphologyAnalyzer(Protocol):
    @property
    def revision(self) -> str: ...

    def analyze(self, words: Iterable[str]) -> dict[str, MorphIdentity | None]: ...


class ApertiumTatarAnalyzer:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        configured = data_dir or os.environ.get("APERTIUM_TAT_DIR")
        self.data_dir = Path(configured) if configured else DEFAULT_APERTIUM_TAT_DIR

    @property
    def revision(self) -> str:
        return APERTIUM_TAT_REVISION

    def analyze(self, words: Iterable[str]) -> dict[str, MorphIdentity | None]:
        unique_words = list(dict.fromkeys(words))
        if not unique_words:
            return {}
        if any(not word or "\n" in word or "\r" in word for word in unique_words):
            raise MorphologyError("morphology input contains an invalid word")

        executable = shutil.which("lt-proc")
        if executable is None:
            raise MorphologyError(
                "lt-proc is unavailable; install apertium-all-dev and run "
                "tools/setup_apertium_tat.sh"
            )
        transducer = self.data_dir / "tat.automorf.bin"
        if not transducer.is_file():
            raise MorphologyError(
                f"Apertium-tat analyzer is missing: {transducer}; "
                "run tools/setup_apertium_tat.sh"
            )

        try:
            completed = subprocess.run(
                [executable, "-w", str(transducer)],
                input="".join(f"{word}\n" for word in unique_words),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MorphologyError(f"Apertium-tat analysis failed: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise MorphologyError(f"Apertium-tat analysis failed: {detail}")

        lines = completed.stdout.splitlines()
        if len(lines) != len(unique_words):
            raise MorphologyError(
                "Apertium-tat returned a different number of lines than requested"
            )
        return {
            word: _unambiguous_identity(line)
            for word, line in zip(unique_words, lines, strict=True)
        }


def default_morphology_analyzer(
    data_dir: str | Path | None = None,
) -> MorphologyAnalyzer:
    return ApertiumTatarAnalyzer(data_dir)


def _unambiguous_identity(line: str) -> MorphIdentity | None:
    units = _lexical_units(line)
    if len(units) != 1:
        return None
    alternatives = _split_unescaped(units[0], "/")
    if len(alternatives) < 2:
        return None

    identities: set[MorphIdentity] = set()
    for analysis in alternatives[1:]:
        if analysis.startswith("*") or "<err_orth>" in analysis:
            continue
        tag_start = _first_unescaped(analysis, "<")
        if tag_start <= 0:
            continue
        tag_end = analysis.find(">", tag_start + 1)
        if tag_end < 0:
            continue
        lemma = _unescape(analysis[:tag_start]).strip().lower()
        part_of_speech = analysis[tag_start + 1 : tag_end].strip()
        if lemma and part_of_speech:
            identities.add(MorphIdentity(lemma, part_of_speech))
    return next(iter(identities)) if len(identities) == 1 else None


def _lexical_units(line: str) -> list[str]:
    units: list[str] = []
    escaped = False
    start: int | None = None
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "^" and start is None:
            start = index + 1
        elif char == "$" and start is not None:
            units.append(line[start:index])
            start = None
        elif start is None and not char.isspace():
            return []
    return units if start is None else []


def _split_unescaped(value: str, separator: str) -> list[str]:
    pieces: list[str] = []
    escaped = False
    start = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == separator:
            pieces.append(value[start:index])
            start = index + 1
    pieces.append(value[start:])
    return pieces


def _first_unescaped(value: str, target: str) -> int:
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == target:
            return index
    return -1


def _unescape(value: str) -> str:
    pieces: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            pieces.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            pieces.append(char)
    if escaped:
        pieces.append("\\")
    return "".join(pieces)
