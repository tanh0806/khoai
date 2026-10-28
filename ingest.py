from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions


BASE_DIR = Path(__file__).resolve().parent

KNOWLEDGE_DIR = BASE_DIR / "knowledge"

VECTOR_DB_DIR = BASE_DIR / "my_vector_db"

COLLECTION_NAME = "khoai_knowledge"



if not KNOWLEDGE_DIR.exists():

    print(
        "LOI: Khong tim thay thu muc knowledge."
    )

    print(
        "Duong dan:",
        KNOWLEDGE_DIR
    )

    raise SystemExit



print(
    "Dang khoi tao mo hinh embedding..."
)


embedding_function = (
    embedding_functions
    .SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
)



print(
    "Dang ket noi ChromaDB..."
)


client = chromadb.PersistentClient(
    path=str(VECTOR_DB_DIR)
)



try:

    client.delete_collection(
        name=COLLECTION_NAME
    )

    print(
        "Da xoa collection cu."
    )

except Exception:

    print(
        "Chua co collection cu."
    )



collection = client.create_collection(

    name=COLLECTION_NAME,

    embedding_function=embedding_function
)


documents = []

ids = []

metadatas = []


txt_files = list(
    KNOWLEDGE_DIR.glob("*.txt")
)


print(
    "Tim thay",
    len(txt_files),
    "file kien thuc."
)


counter = 1


for file in txt_files:

    print(
        "Dang doc:",
        file.name
    )


    text = file.read_text(
        encoding="utf-8"
    )


 
    chunks = [

        chunk.strip()

        for chunk in text.split("\n\n")

        if chunk.strip()
    ]


    print(
        "  So doan:",
        len(chunks)
    )


    for chunk in chunks:

        documents.append(
            chunk
        )


        ids.append(
            f"doc_{counter}"
        )


        metadatas.append({

            "source":
                file.name

        })


        counter += 1



if not documents:

    print(
        "LOI: Khong co kien thuc de nap."
    )

    print(
        "Hay kiem tra cac file trong:",
        KNOWLEDGE_DIR
    )

    raise SystemExit



print()

print(
    "Dang tao vector va nap vao ChromaDB..."
)


collection.add(

    documents=documents,

    ids=ids,

    metadatas=metadatas
)


print()

print(
    "=" * 60
)

print(
    "NAP DU LIEU RAG THANH CONG"
)

print(
    "=" * 60
)

print(
    "So file:",
    len(txt_files)
)

print(
    "So doan kien thuc:",
    len(documents)
)

print(
    "Collection:",
    COLLECTION_NAME
)

print(
    "Vector DB:",
    VECTOR_DB_DIR
)

print(
    "=" * 60
)