import os
from functools import lru_cache

from dotenv import load_dotenv
from google.genai.errors import APIError as GenAiAPIError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleGenerativeAIError, GoogleRateLimitError

from app.config import BASE_DIR

load_dotenv(dotenv_path=BASE_DIR / ".env")


class MissingApiKeyError(RuntimeError):
    pass


def classify_google_error(exc: Exception):
    """Every Gemini call (chat or embeddings) can fail with a transient
    Google-side error — most commonly free-tier rate limiting, since a
    single scan/chat turn can fire off several sequential Gemini calls
    (tool-calling + per-agent structured output + embeddings). Returns
    (status_code, user-facing message) for anything coming from the
    Google client, or None if this isn't one of those errors — callers
    fall back to a generic 500 in that case, but should always catch
    broadly so nothing ever surfaces FastAPI's bare "Internal Server
    Error" to the frontend, which has no way to recover from that.

    There are two unrelated exception hierarchies here, not one:
    langchain_google_genai wraps errors from the CHAT model into its own
    GoogleGenerativeAIError/GoogleRateLimitError classes, but the
    embeddings client (used by every RAG lookup) raises the underlying
    google-genai SDK's APIError directly, unwrapped — a rate limit or
    server error during RAG was falling through this function entirely
    until both hierarchies were checked here."""
    if isinstance(exc, GoogleRateLimitError):
        return 429, (
            "Vriddhi is temporarily rate-limited by the Gemini API — this usually means the free-tier quota "
            "(requests per minute, or the daily cap) was hit, since a single scan or chat turn makes several "
            "Gemini calls in a row. Wait a minute and try again."
        )
    if isinstance(exc, GoogleGenerativeAIError):
        return 503, f"The Gemini API is temporarily unavailable ({exc.__class__.__name__}). Please try again shortly."
    if isinstance(exc, GenAiAPIError):
        code = getattr(exc, "code", None)
        if code == 429:
            return 429, (
                "Vriddhi is temporarily rate-limited by the Gemini API (embeddings/RAG call) — this usually means "
                "the free-tier quota was hit, since a single scan or chat turn makes several Gemini calls in a "
                "row. Wait a minute and try again."
            )
        return 503, f"The Gemini API is temporarily unavailable (HTTP {code or 'unknown'}). Please try again shortly."
    return None


@lru_cache(maxsize=1)
def get_llm():
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise MissingApiKeyError(
            "GOOGLE_API_KEY is not set. Add it to server/.env (GOOGLE_API_KEY=your-key) and restart the server."
        )
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0.2)
