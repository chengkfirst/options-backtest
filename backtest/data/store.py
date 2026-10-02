"""A local parquet cache.

The cache key must cover **every dimension that changes the result**. Leave one
out and entries get crossed silently — ask for adjusted prices and receive traded
prices; the data looks fine and every conclusion is wrong. So the key carries the
symbol, the date range, the source, the price type and the generation parameters,
and any difference in any of them is a miss.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

DEFAULT_CACHE_DIR = Path(".cache")


def _fingerprint(parts: dict[str, Any]) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CacheKey:
    """The full identity of a cache entry."""

    kind: str
    symbol: str
    params: dict[str, Any]

    def filename(self) -> str:
        return f"{self.kind}__{self.symbol}__{_fingerprint(self.params)}.parquet"


class ParquetCache:
    def __init__(self, root: Path | str = DEFAULT_CACHE_DIR, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    def get_or_load(
        self, key: CacheKey, loader: Callable[[], pd.DataFrame], *, refresh: bool = False
    ) -> pd.DataFrame:
        """Read from disk on a hit; otherwise call ``loader`` and write it out.

        ``refresh=True`` ignores the cache, refetches, and overwrites the entry.
        """
        if not self.enabled:
            self._misses += 1
            return loader()

        path = self.root / key.filename()
        if path.exists() and not refresh:
            self._hits += 1
            return pd.read_parquet(path)

        self._misses += 1
        frame = loader()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file and rename, so a failure part-way through
        # cannot leave half a file that a later run treats as a valid cache entry.
        tmp = path.with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=True)
        tmp.replace(path)
        return frame
