from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...


def tokenize_code(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fff]", text)
    tokens: list[str] = []
    for token in raw_tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]", token):
            tokens.append(token)
            continue
        snake_parts = token.replace("_", " ").split()
        for part in snake_parts:
            camel_parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part).split()
            tokens.extend(item.lower() for item in camel_parts if item)
    chinese = [token for token in tokens if re.fullmatch(r"[\u4e00-\u9fff]", token)]
    tokens.extend(a + b for a, b in zip(chinese, chinese[1:]))
    return tokens


class HashEmbeddingProvider:
    """Deterministic offline baseline; replaceable by a semantic embedding model."""

    def __init__(self, dimension: int = 256) -> None:
        if dimension < 32:
            raise ValueError("embedding dimension must be at least 32")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        counts = Counter(tokenize_code(text))
        vector = [0.0] * self.dimension
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector

