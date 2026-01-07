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
from typing import Any

import chromadb
import numpy as np
from bm25s import BM25, tokenize
from chromadb.api.types import Embeddings
from chromadb.config import Settings
from chromadb.utils.embedding_functions import EmbeddingFunction
from dotenv import load_dotenv
from google import genai
from google.genai import types
from itrx import Itr
from Stemmer import Stemmer

load_dotenv()


client = genai.Client()

class BM25Wrapper:
    def __init__(self, corpus: list[str]) -> None:
        self.stemmer = Stemmer("english")
        self.corpus = corpus
        corpus_tokens = tokenize(corpus, stopwords="en", stemmer=self.stemmer)
        # Create the BM25 model and index the corpus
        self.retriever = BM25()
        self.retriever.index(corpus_tokens)

    def retrieve(self, query: str, k: int) -> list[tuple[str, dict[str, Any], float]]:
        query_tokens = tokenize(query, stemmer=self.stemmer)

        # # Get top-k results as a tuple of (doc ids, scores). Both are arrays of shape (n_queries, k).
        # # To return docs instead of IDs, set the `corpus=corpus` parameter.
        indices, scores = self.retriever.retrieve(query_tokens, k=5)

        results = []

        # NB 1.25 picked out of thin air based on a single query
        for i in range(indices.shape[1]):
            idx, score = indices[0, i], scores[0, i]
            results.append((self.corpus[idx], {"source": "BM25", "chunk_index": int(idx)}, 1.25 / score))
        return results



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
        "Firstly determine the relevant facts from the context, then "
        "answer concisely using only those facts, including citations like [0], [1] referencing the blocks above."
    )

    resp = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt, config=types.GenerateContentConfig(temperature=temperature)
    )
    return resp.text


# -------------- Orchestration --------------
def build_rag_from_chunks(json_path: Path, collection_name: str = "pdf_rag", persist_dir: str = "./chroma_store"):
    with json_path.open() as fd:
        passages = json.load(fd)

    # Create the BM25 model and index the corpus
    retriever = BM25Wrapper(passages)

    # Create collection with a DOCUMENT-optimized embedding function
    ef_docs = GeminiEmbedding(output_dim=768, task_type="RETRIEVAL_DOCUMENT")
    collection = get_collection(persist_dir, collection_name, ef_docs)

    # If collection is empty (fresh), add passages
    if collection.count() == 0:
        index_passages(collection, passages, source_name=os.path.basename(json_path))
        print(f"Indexed {len(passages)} chunks into Chroma collection '{collection_name}'.")
    else:
        print(f"Collection '{collection_name}' already has {collection.count()} items. Skipping re-index.")

    return passages, collection, retriever


def answer_question(query: str, collection, bm25_retriever, top_k: int = 5) -> tuple[str, list]:
    retrieved = retrieve(collection, query, top_k=top_k)

    retrieved_bm25 = bm25_retriever.retrieve(query, k = 5)

    ret_indices = [r[1]["chunk_index"] for r in retrieved]
    for r in retrieved_bm25:
        if r[1]["chunk_index"] not in ret_indices:
            retrieved.append(r)

    answer = answer_with_gemini(query, retrieved, temperature=0.0)
    return answer, retrieved


# TODO
# [X] implement hybrid retrieval - vector + keyword score (BM25/keyword/regex)
# [ ] rewrite/expand query
# [ ] multi-vector embeddings
# [ ] improve chunking
# [ ] implement reranking - cross-encoder/LLM step. this should be a big win
# [ ] Summarize retrieved chunks with respect to the query
# [ ] Dynamic top-n
# [X] Cite-Then-Answer Prompting: list relevant facts, then answer using only those facts
# [ ] allow looping - LLM requests more context, reject insufficient evidence, ask for clarification
# [ ] self verification

if __name__ == "__main__":
    try:
        # json chunks should be a list of strings
        if len(sys.argv) < 3:
            print(f'Usage: python {__file__} <json-chunks> "<question>"')
            sys.exit(1)

        json_path = Path(sys.argv[1])
        question = sys.argv[2]

        _, embeddings, keywords = build_rag_from_chunks(json_path, collection_name="pdf_rag", persist_dir="./chroma_store")

        answer, hits = answer_question(question, embeddings, keywords, top_k=5)

        print("\n--- Retrieved Chunks ---\n")
        for i, (doc, meta, dist) in enumerate(hits):
            print(f"[{i}] {doc[:20]} {meta}  distance={dist:.4f}")

        print("\n--- Answer ---\n")
        print(answer)
    except Exception as e:
        print(e)