import numpy as np


class VectorStore:
    """A small in-memory vector store: real embeddings (Gemini's
    text-embedding-004 via GoogleGenerativeAIEmbeddings), cosine similarity
    over numpy arrays. No external vector DB is warranted at this dataset's
    size, but retrieval is genuine nearest-neighbor search over embeddings —
    not a keyword or dict lookup dressed up as RAG."""

    def __init__(self, embeddings_model):
        self.embeddings_model = embeddings_model
        self.docs = []
        self._vectors = None

    def add_documents(self, docs):
        """docs: list of {"id": str, "text": str, "metadata": dict}"""
        if not docs:
            return
        vecs = np.array(self.embeddings_model.embed_documents([d["text"] for d in docs]), dtype=np.float32)
        self.docs.extend(docs)
        self._vectors = vecs if self._vectors is None else np.vstack([self._vectors, vecs])

    def similarity_search(self, query, k=3, filter_fn=None):
        if not self.docs:
            return []
        candidate_idxs = [i for i, d in enumerate(self.docs) if filter_fn is None or filter_fn(d)]
        if not candidate_idxs:
            return []

        query_vec = np.array(self.embeddings_model.embed_query(query), dtype=np.float32)
        sub_vectors = self._vectors[candidate_idxs]

        norms = np.linalg.norm(sub_vectors, axis=1) * (np.linalg.norm(query_vec) + 1e-8)
        sims = (sub_vectors @ query_vec) / (norms + 1e-8)

        top_k = min(k, len(candidate_idxs))
        ranked = np.argsort(-sims)[:top_k]
        return [(self.docs[candidate_idxs[i]], float(sims[i])) for i in ranked]
