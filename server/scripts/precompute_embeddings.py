"""Precomputes embeddings for every static RAG corpus and writes them to
app/rag/precomputed/<key>.json. Run this once after the dataset changes:

    cd server
    ./venv/Scripts/python.exe scripts/precompute_embeddings.py

The app loads these files instead of calling the embeddings API at
runtime for static content — see knowledge_base.py's _build(). Nothing
breaks if this script is never run (it falls back to a live call), it
just means every cold start pays for re-embedding content that never
changes, which matters on Render's free tier (process restarts on every
idle spin-down) and against a tight free-tier rate limit.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.loader import load_data
from app.rag.embeddings import get_embeddings
from app.rag.knowledge_base import PRECOMPUTED_DIR, STATIC_DOC_BUILDERS


def main():
    data = load_data()
    embeddings = get_embeddings()
    PRECOMPUTED_DIR.mkdir(parents=True, exist_ok=True)

    for key, build_docs in STATIC_DOC_BUILDERS.items():
        docs = build_docs(data)
        print(f"{key}: embedding {len(docs)} document(s)...")
        vectors = embeddings.embed_documents([d["text"] for d in docs])
        out_path = PRECOMPUTED_DIR / f"{key}.json"
        out_path.write_text(json.dumps({"docs": docs, "vectors": vectors}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> wrote {out_path} ({len(vectors)} vectors, dim {len(vectors[0]) if vectors else 0})")

    print("Done.")


if __name__ == "__main__":
    main()
