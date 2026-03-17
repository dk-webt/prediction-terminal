from .protocol import EventMatcher
from .v1 import GeminiFuzzyMatcher

__all__ = ["EventMatcher", "GeminiFuzzyMatcher", "default_matcher"]


def default_matcher() -> EventMatcher:
    """Return the default matcher (V1: Gemini embeddings + fuzzy fallback)."""
    return GeminiFuzzyMatcher()
