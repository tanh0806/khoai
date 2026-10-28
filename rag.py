from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions


BASE_DIR = Path(__file__).resolve().parent
VECTOR_DB_DIR = BASE_DIR / "my_vector_db"
COLLECTION_NAME = "khoai_knowledge"

embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))

try:
    collection = client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
    )
except Exception:
    collection = None


@lru_cache(maxsize=256)
def _search_cached(question, n_results):
    """Cache tối đa 256 câu hỏi RAG đã tìm để câu hỏi lặp lại trả nhanh."""
    if collection is None:
        return ""

    question = str(question or "").strip()
    if not question:
        return ""

    try:
        count = collection.count()
        if count <= 0:
            return ""
        k = max(1, min(int(n_results), count))

        result = collection.query(
            query_texts=[question],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]

        contexts = []
        for document, metadata in zip(documents, metadatas):
            source = (metadata or {}).get("source", "khong_ro")
            contexts.append(f"Nguồn: {source}\n{document}")

        return "\n\n--------------------\n\n".join(contexts)

    except Exception as e:
        print("[RAG] Lỗi tìm kiếm:", e)
        return ""


def search_knowledge(question, n_results=3):
    """Hàm dùng từ app.py. Mặc định chỉ lấy 3 đoạn gần nhất để giảm độ trễ."""
    return _search_cached(str(question or "").strip(), int(n_results))


def clear_rag_cache():
    """Gọi hàm này sau khi chạy lại ingest.py trong cùng một process (nếu cần)."""
    _search_cached.cache_clear()


if __name__ == "__main__":
    print(search_knowledge("C08 là gì?", n_results=3))
