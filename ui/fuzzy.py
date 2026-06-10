"""
Fuzzy matcher — port of toad/fuzzy.py.

Returns ALL possible matches (not just the first one), so we can pick the
best by `score()` heuristic that boosts:
  - matching first letters of words
  - contiguous runs of matched characters
"""

from __future__ import annotations

from functools import lru_cache
from operator import itemgetter
from re import finditer
from typing import Iterable, Sequence

from textual.cache import LRUCache


class FuzzySearch:
    def __init__(
        self, case_sensitive: bool = False, *, cache_size: int = 1024 * 4
    ) -> None:
        self.case_sensitive = case_sensitive
        self.cache: LRUCache[tuple[str, str], tuple[float, Sequence[int]]] = LRUCache(
            cache_size
        )

    def match(self, query: str, candidate: str) -> tuple[float, Sequence[int]]:
        cache_key = (query, candidate)
        if cache_key in self.cache:
            return self.cache[cache_key]
        default: tuple[float, Sequence[int]] = (0.0, [])
        result = max(self._match(query, candidate), key=itemgetter(0), default=default)
        self.cache[cache_key] = result
        return result

    @classmethod
    @lru_cache(maxsize=1024)
    def get_first_letters(cls, candidate: str) -> frozenset[int]:
        return frozenset({m.start() for m in finditer(r"\w+", candidate)})

    def score(self, candidate: str, positions: Sequence[int]) -> float:
        first_letters = self.get_first_letters(candidate)
        offset_count = len(positions)
        score: float = offset_count + len(first_letters.intersection(positions))

        groups = 1
        last_offset, *offsets = positions
        for offset in offsets:
            if offset != last_offset + 1:
                groups += 1
            last_offset = offset

        normalized = (offset_count - (groups - 1)) / offset_count
        score *= 1 + (normalized * normalized)
        return score

    def _match(
        self, query: str, candidate: str
    ) -> Iterable[tuple[float, Sequence[int]]]:
        if not self.case_sensitive:
            candidate = candidate.casefold()
            query = query.casefold()

        letter_positions: list[list[int]] = []
        position = 0
        for offset, letter in enumerate(query):
            last_index = len(candidate) - offset
            positions: list[int] = []
            letter_positions.append(positions)
            index = position
            while (location := candidate.find(letter, index)) != -1:
                positions.append(location)
                index = location + 1
                if index >= last_index:
                    break
            if not positions:
                yield (0.0, ())
                return
            position = positions[0] + 1

        possible: list[list[int]] = []
        query_length = len(query)

        def collect(offsets: list[int], positions_index: int) -> None:
            for offset in letter_positions[positions_index]:
                if not offsets or offset > offsets[-1]:
                    new_offsets = [*offsets, offset]
                    if len(new_offsets) == query_length:
                        possible.append(new_offsets)
                    else:
                        collect(new_offsets, positions_index + 1)

        collect([], 0)
        for offsets in possible:
            yield self.score(candidate, offsets), offsets
