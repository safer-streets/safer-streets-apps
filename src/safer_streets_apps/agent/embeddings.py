"""
RAG using Google Gemini + ChromaDB (persistent local vector store).
Requires GEMINI_API_KEY
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import chromadb
import numpy as np
from chromadb.api.types import Embeddings
from chromadb.config import Settings
from chromadb.utils.embedding_functions import EmbeddingFunction
from dotenv import load_dotenv
from google import genai
from google.genai import types
from itrx import Itr

load_dotenv()


client = genai.Client()

# TODO switch to mongo?
# Chroma embedding functor (Gemini)
class GeminiEmbedding(EmbeddingFunction):
    """
    Custom embedding function for Chroma that uses Google's gemini-embedding-001.
    We use task_type to optimize for retrieval and (optionally) a compact dimension.
    """

    def __init__(self, output_dim: int = 768, task_type: str = "RETRIEVAL_DOCUMENT"):
        self.output_dim = output_dim
        self.task_type = task_type

    def __call__(self, texts: list[str]) -> Embeddings:
        # Batch embed with Gemini; returns normalized vectors.
        results = []
        for batch in Itr(texts).batched(100):
            result = client.models.embed_content(
                model="gemini-embedding-001",
                contents=batch,
                config=types.EmbedContentConfig(output_dimensionality=self.output_dim, task_type=self.task_type),
            )
            vecs = np.array([e.values for e in result.embeddings], dtype=np.float32)
            # Normalize (cosine-ready); Chroma uses cosine by default, normalization is fine
            norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
            vecs = vecs / norms
            results.extend(vecs.tolist())
            # need to throttle if over 100 requests
            if len(texts) > 100:
                time.sleep(61)
        return results


# Build / load Chroma collection
def get_collection(persist_dir: str, name: str, ef: EmbeddingFunction):
    os.makedirs(persist_dir, exist_ok=True)
    client_chroma = chromadb.Client(Settings(is_persistent=True, persist_directory=persist_dir))
    return client_chroma.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},  # cosine similarity
    )


def index_passages(collection, passages: list[str], source_name: str):
    ids = [str(uuid.uuid4()) for _ in passages]
    metadatas = [{"source": source_name, "chunk_index": i} for i in range(len(passages))]
    collection.add(ids=ids, documents=passages, metadatas=metadatas)


# Retrieval + Answering
def retrieve(collection, query: str, top_k: int = 5) -> list[tuple[str, dict, float]]:
    # Use a query-specific embedding task type for better matching
    q_embed_fn = GeminiEmbedding(output_dim=768, task_type="RETRIEVAL_QUERY")
    q_vec = q_embed_fn([query])[0]  # single vector
    results = collection.query(
        query_embeddings=[q_vec], n_results=top_k, include=["documents", "metadatas", "distances"]
    )
    # Chroma returns lists (one per query); we have a single query
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]
    return list(zip(docs, metas, dists, strict=True))


def answer_with_gemini(query: str, retrieved: list[tuple[str, dict, float]], temperature: float = 0.0) -> str:
    # Build a grounded prompt with inline citations
    context_blocks = []
    for i, (doc, meta, _dist) in enumerate(retrieved):
        tag = f"[{i}] {meta.get('source', 'doc')}#chunk{meta.get('chunk_index', i)}"
        context_blocks.append(f"{tag}\n{doc}")

    context = "\n\n".join(context_blocks) if context_blocks else "(no matches)"
    prompt = (
        "You are a strict, citation-driven assistant. Use ONLY the provided context.\n"
        "If the answer isn't present, say you don't know.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {query}\n"
        "Answer concisely and include citations like [0], [1] referencing the blocks above."
    )

    resp = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt, config=types.GenerateContentConfig(temperature=temperature)
    )
    return resp.text


# -------------- Orchestration --------------
def build_rag_from_chunks(json_path: Path, collection_name: str = "pdf_rag", persist_dir: str = "./chroma_store"):
    with json_path.open() as fd:
        passages = json.load(fd)

    # Create collection with a DOCUMENT-optimized embedding function
    ef_docs = GeminiEmbedding(output_dim=768, task_type="RETRIEVAL_DOCUMENT")
    collection = get_collection(persist_dir, collection_name, ef_docs)

    # If collection is empty (fresh), add passages
    if collection.count() == 0:
        index_passages(collection, passages, source_name=os.path.basename(json_path))
        print(f"Indexed {len(passages)} chunks into Chroma collection '{collection_name}'.")
    else:
        print(f"Collection '{collection_name}' already has {collection.count()} items. Skipping re-index.")

    return passages, collection


def answer_question(query: str, collection, top_k: int = 5) -> tuple[str, list]:
    retrieved = retrieve(collection, query, top_k=top_k)
    answer = answer_with_gemini(query, retrieved, temperature=0.0)
    return answer, retrieved


if __name__ == "__main__":
    # json chunks should be a list of strings
    if len(sys.argv) < 3:
        print(f'Usage: python {__file__} <json-chunks> "<question>"')
        sys.exit(1)

    json_path = Path(sys.argv[1])
    question = sys.argv[2]

    _, collection = build_rag_from_chunks(json_path, collection_name="pdf_rag", persist_dir="./chroma_store")
    answer, hits = answer_question(question, collection, top_k=5)

    print("\n--- Retrieved Chunks ---\n")
    for i, (doc, meta, dist) in enumerate(hits):
        print(f"[{i}] {meta}  distance={dist:.4f}")
        print(doc[:400].replace("\n", " ") + ("..." if len(doc) > 400 else ""))
        print()

    print("\n--- Answer ---\n")
    print(answer)
