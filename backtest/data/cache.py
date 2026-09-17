"""本地 parquet 缓存。

缓存键必须涵盖**一切影响结果的维度**。少涵盖一个，就会出现"请求复权价却拿到实际
成交价"这类静默串用——数据看上去正常，结论全错。因此键里带上标的、区间、数据源、
价格类型与生成参数，任一不同即不命中。
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
    """缓存条目的完整身份。"""

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
        """命中则读盘，否则调用 ``loader`` 并落盘。

        ``refresh=True`` 强制忽略缓存重新获取，并覆盖原条目。
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
        # 先写临时文件再改名，避免中途失败留下半个文件被后续当成有效缓存
        tmp = path.with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=True)
        tmp.replace(path)
        return frame
