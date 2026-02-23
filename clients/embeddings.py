import time
import os
import numpy as np
from google import genai
from google.genai.errors import ClientError
from config import GEMINI_API_KEY

MODEL = "gemini-embedding-001"
_client: genai.Client | None = None

# Gemini free/paid tier: 3000 texts/min via embed_content
# Keep batches small and add backoff to stay within quota
BATCH_SIZE = 80
MAX_RETRIES = 5


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set. Add it to your .env file.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _embed_batch(client: genai.Client, texts: list[str], retry: int = 0) -> list[list[float]]:
    """Embed one batch with exponential backoff on rate-limit errors."""
    try:
        resp = client.models.embed_content(model=MODEL, contents=texts)
        return [v.values for v in resp.embeddings]
    except ClientError as exc:
        if exc.status_code == 429 and retry < MAX_RETRIES:
            # Parse suggested retry delay from error, default to 30s
            wait = 30
            msg = str(exc)
            import re
            m = re.search(r"retry[^\d]*(\d+)", msg, re.IGNORECASE)
            if m:
                wait = int(m.group(1)) + 2
            time.sleep(wait)
            return _embed_batch(client, texts, retry + 1)
        raise


def embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed a list of texts using Gemini. Returns an (N, D) float32 array.
    Batches requests to stay within API limits, retrying on rate-limit errors.
    """
    client = _get_client()
    all_vectors: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        vectors = _embed_batch(client, batch)
        all_vectors.extend(vectors)
        # Small sleep between batches to stay under 3000 texts/min
        if i + BATCH_SIZE < len(texts):
            time.sleep(1.6)

    return np.array(all_vectors, dtype=np.float32)


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between every row of A and every row of B.
    Returns an (len(a), len(b)) matrix with values in [-1, 1].
    """
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return a_norm @ b_norm.T
