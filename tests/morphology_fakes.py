from __future__ import annotations

from collections.abc import Iterable

from tatar_preannotator.morphology import MorphIdentity


class FakeMorphologyAnalyzer:
    revision = "test-apertium-tat"

    def __init__(
        self,
        identities: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.identities = {
            word: MorphIdentity(*identity)
            for word, identity in (identities or {}).items()
        }
        self.calls: list[tuple[str, ...]] = []

    def analyze(self, words: Iterable[str]) -> dict[str, MorphIdentity | None]:
        unique = tuple(dict.fromkeys(words))
        self.calls.append(unique)
        return {word: self.identities.get(word) for word in unique}
