import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import BASE_DIR

load_dotenv(dotenv_path=BASE_DIR / ".env")


class MissingApiKeyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_llm():
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise MissingApiKeyError(
            "GOOGLE_API_KEY is not set. Add it to server/.env (GOOGLE_API_KEY=your-key) and restart the server."
        )
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0.2)
