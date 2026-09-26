import os
from functools import lru_cache

from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.agents.llm import MissingApiKeyError


@lru_cache(maxsize=1)
def get_embeddings():
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise MissingApiKeyError(
            "GOOGLE_API_KEY is not set. Add it to server/.env (GOOGLE_API_KEY=your-key) and restart the server."
        )
    model_name = os.environ.get("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001").strip() or "models/gemini-embedding-001"
    return GoogleGenerativeAIEmbeddings(model=model_name, google_api_key=api_key)
