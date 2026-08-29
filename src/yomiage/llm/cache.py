"""本文を保持しないhash key型LLM cache。"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any


class LLMCache:
    """プロセス内の上限付きLRU cache。keyには本文を含めない。"""

    def __init__(self, max_entries: int = 4096) -> None:
        self.max_entries = max(1, max_entries)
        self._values: OrderedDict[str, Any] = OrderedDict()

    @staticmethod
    def key(namespace: str, value: object) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{namespace}:{digest}"

    def get(self, key: str) -> Any | None:
        if key not in self._values:
            return None
        value = self._values.pop(key)
        self._values[key] = value
        return value

    def put(self, key: str, value: Any) -> None:
        self._values.pop(key, None)
        self._values[key] = value
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)
