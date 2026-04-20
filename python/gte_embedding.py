from __future__ import annotations

import asyncio
from typing import Sequence

from sentence_transformers import SentenceTransformer
import torch

# プロセス内で複数の asyncio task が同一モデルインスタンスを並列呼び出しする場合の
# thread-safety を保証するためのロック（MPS/CPU 問わず適用）
_EMBED_LOCK = asyncio.Lock()


DEFAULT_EMBEDDING_MODEL = "cl-nagoya/ruri-v3-30m"


def _pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class RuriEmbeddingModel:
    """ruri-v3-30m を使う埋め込みラッパー。"""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 32,
        max_length: int = 512,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = device or _pick_device()
        self._model = SentenceTransformer(
            model_name_or_path=model_name,
            device=self.device,
            trust_remote_code=True,
        )
        # MPS/CUDA では半精度にしてメモリ使用量を抑えつつ演算密度を上げる。
        if self.device in {"cuda", "mps"}:
            self._model.half()
        if hasattr(self._model, "max_seq_length"):
            self._model.max_seq_length = self.max_length

    def embed_sync(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        with torch.inference_mode():
            embeddings = self._model.encode(
                list(texts),
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return embeddings.tolist()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """thread-safe な非同期埋め込み。同一プロセス内では直列化する。"""
        async with _EMBED_LOCK:
            return await asyncio.to_thread(self.embed_sync, list(texts))


# 既存参照互換
GteEmbeddingModel = RuriEmbeddingModel