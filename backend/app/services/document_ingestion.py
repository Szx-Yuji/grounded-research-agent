import uuid
from pathlib import Path

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
)
from langchain_core.documents import Document
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
)

from app.core.config import settings

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


def get_stored_file_path(storage_key: str) -> Path:
    storage_root = settings.DOCUMENT_STORAGE_DIR.resolve()
    file_path = (storage_root / storage_key).resolve()

    if not file_path.is_relative_to(storage_root):
        raise ValueError("Invalid document storage key")

    if not file_path.is_file():
        raise FileNotFoundError("Document file not found")

    return file_path


def load_document(file_path: Path) -> list[Document]:
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        loader = PyPDFLoader(str(file_path))

    elif suffix in {".txt", ".md"}:
        loader = TextLoader(
            str(file_path),
            encoding="utf-8",
            autodetect_encoding=True,
        )

    else:
        raise ValueError(f"Unsupported document type: {suffix}")

    return loader.load()


def split_document(
    *,
    storage_key: str,
    original_filename: str,
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
) -> list[Document]:
    file_path = get_stored_file_path(storage_key)
    documents = load_document(file_path)

    for document in documents:
        document.metadata.update(
            {
                "source": original_filename,
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(document_id),
            }
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    chunks = splitter.split_documents(documents)

    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index

    return chunks
