from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

from openai import OpenAI


class EmbeddingProvider(Protocol):
    dimension: int
    provider_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...

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
        self.provider_id = f"hash-blake2b-v1:{dimension}"

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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query)


class OpenAICompatibleEmbeddingProvider:
    """Semantic embedding provider for OpenAI-compatible embedding endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        dimension: int = 1024,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        batch_size: int = 32,
    ) -> None:
        if not api_key:
            raise ValueError("embedding API key is required")
        if not model:
            raise ValueError("embedding model is required")
        if dimension < 1 or batch_size < 1:
            raise ValueError("embedding dimension and batch size must be positive")
        self.dimension = dimension
        self.batch_size = batch_size
        self.provider_id = f"openai-compatible:{base_url or 'default'}:{model}:{dimension}"
        self._model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            response = self._client.embeddings.create(
                model=self._model,
                input=texts[start : start + self.batch_size],
                dimensions=self.dimension,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(self._normalize(list(item.embedding)) for item in ordered)
        if len(vectors) != len(texts):
            raise RuntimeError("embedding endpoint returned an unexpected vector count")
        return vectors

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_documents([query])
        return vectors[0]

    def embed(self, text: str) -> list[float]:
        return self.embed_query(text)

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:
            return vector
        return [value / norm for value in vector]
