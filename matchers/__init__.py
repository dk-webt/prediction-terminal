from .protocol import EventMatcher
from .v1 import GeminiFuzzyMatcher
from .v2 import GeminiRichMatcher

__all__ = ["EventMatcher", "GeminiFuzzyMatcher", "GeminiRichMatcher", "default_matcher"]


def default_matcher() -> EventMatcher:
    """Return the default matcher (V2: rich embeddings + composite scoring)."""
    return GeminiRichMatcher()
