import os
from functools import lru_cache

from dotenv import load_dotenv
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
    Error" to the frontend, which has no way to recover from that."""
    if isinstance(exc, GoogleRateLimitError):
        return 429, (
            "Vriddhi is temporarily rate-limited by the Gemini API — this usually means the free-tier quota "
            "(requests per minute, or the daily cap) was hit, since a single scan or chat turn makes several "
            "Gemini calls in a row. Wait a minute and try again."
        )
    if isinstance(exc, GoogleGenerativeAIError):
        return 503, f"The Gemini API is temporarily unavailable ({exc.__class__.__name__}). Please try again shortly."
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
