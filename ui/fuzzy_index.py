"""
Trigram-based path index for fast prefilter before scoring.

Port of toad/fuzzy_index.py — the trigram bucket lookup reduces millions
of paths to a manageable set the slower scoring stage can handle.
"""

from __future__ import annotations

import asyncio
from asyncio import Lock
from collections import Counter, defaultdict
from itertools import islice
from operator import itemgetter


class FuzzyIndex:
    def __init__(self) -> None:
        self._paths: list[str] = []
        self._normalized_paths: list[str] = []
        self._index: dict[str, set[int]] = {}
        self._weights_cache: dict[str, dict[int, float]] = {}
        self._path_counter_cache: dict[str, Counter[str]] = {}
        self._lock = Lock()

    async def update_paths(self, paths: list[str]) -> None:
        async with self._lock:
            self._paths = paths
            self._normalized_paths = await asyncio.to_thread(
                self._normalize_paths, paths
            )
            self._index = await asyncio.to_thread(
                self._build_trigram_index, self._normalized_paths
            )

    @classmethod
    def _normalize_paths(cls, paths: list[str]) -> list[str]:
        return list(map(str.casefold, paths))

    @classmethod
    def _extract_trigrams(cls, text: str) -> set[str]:
        padded = f"  {text} "
        return {padded[i : i + 3] for i in range(len(padded) - 2)}

    @classmethod
    def _build_trigram_index(cls, paths: list[str]) -> dict[str, set[int]]:
        index: dict[str, set[int]] = defaultdict(set)
        for path_index, path in enumerate(paths):
            for trigram in cls._extract_trigrams(path):
                index[trigram].add(path_index)
        return index

    def _find_candidates(
        self,
        query: str,
        min_trigram_overlap: float = 0.3,
    ) -> list[tuple[str, str]]:
        MAX_CANDIDATES = 2000
        query = query.casefold()
        ql = len(query)

        if ql == 1:
            slash_query = f"/{query}"
            return list(
                islice(
                    (
                        (p, np)
                        for p, np in zip(self._paths, self._normalized_paths)
                        if np.startswith(query) or slash_query in np
                    ),
                    None,
                    MAX_CANDIDATES,
                )
            )

        if ql <= 3:
            qset = set(query)
            return list(
                islice(
                    (
                        (p, np)
                        for p, np in zip(self._paths, self._normalized_paths)
                        if qset.issubset(np)
                    ),
                    None,
                    MAX_CANDIDATES,
                )
            )

        index = self._index
        qtrigrams = self._extract_trigrams(query)
        buckets = [index[t] for t in qtrigrams if t in index]

        counts = Counter(
            sorted(p for bucket in buckets for p in bucket)
        )
        threshold = len(qtrigrams) * min_trigram_overlap
        unique = dict.fromkeys(
            islice(
                (
                    (self._paths[i], self._normalized_paths[i])
                    for i, c in counts.items()
                    if c >= threshold
                ),
                None,
                MAX_CANDIDATES,
            )
        )
        return list(unique.keys())

    @classmethod
    def make_weights(cls, path: str) -> dict[int, float]:
        weights: dict[int, float] = dict.fromkeys(
            range(path.rfind("/") + 1, len(path)), 1.0
        ) | {0: 1.0}
        index = path.find("/", None, -1)
        while index != -1:
            weights[index + 1] = weights.setdefault(index + 1, 1.0) + 1.0
            index = path.find("/", index + 1, -1)
        return weights

    async def search(self, query: str) -> list[str]:
        nq = query.casefold()
        qcounter = Counter(nq)
        scores: list[tuple[float, str]] = []
        TOP = 1000

        async with self._lock:
            for path, npath in self._find_candidates(nq):
                if (pc := self._path_counter_cache.get(npath)) is None:
                    pc = self._path_counter_cache[npath] = Counter(npath)
                if qcounter - pc:
                    continue

                if (w := self._weights_cache.get(npath)) is None:
                    w = self._weights_cache[npath] = self.make_weights(npath)

                score = sum(w.get(nq.rfind(c), 0.0) for c in npath)
                if npath.endswith(nq):
                    score *= 2
                if npath.endswith(f"/{nq}"):
                    score *= 2
                scores.append((score, path))

            scores.sort(reverse=True)
            return list(map(itemgetter(1), scores[:TOP]))
