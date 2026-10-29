import uuid

from langchain_chroma import Chroma
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import settings


def create_chat_model() -> ChatOpenAI:
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    if not settings.DEEPSEEK_BASE_URL:
        raise RuntimeError("DEEPSEEK_BASE_URL is not configured")

    return ChatOpenAI(
        model=settings.DEEPSEEK_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=0,
        timeout=60,
        max_retries=2,
    )


def create_embeddings() -> OpenAIEmbeddings:
    if not settings.ZHIPUAI_API_KEY:
        raise RuntimeError("ZHIPUAI_API_KEY is not configured")

    return OpenAIEmbeddings(
        model=settings.ZHIPUAI_EMBEDDING_MODEL,
        api_key=settings.ZHIPUAI_API_KEY,
        base_url=settings.ZHIPUAI_EMBEDDING_BASE_URL,
        request_timeout=30,
        max_retries=1,
    )


def get_collection_name(
    knowledge_base_id: uuid.UUID,
) -> str:
    return f"kb_{knowledge_base_id.hex}"


def create_vectorstore(
    knowledge_base_id: uuid.UUID,
    *,
    with_embeddings: bool = True,
) -> Chroma:
    settings.CHROMA_PERSIST_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    return Chroma(
        collection_name=get_collection_name(knowledge_base_id),
        persist_directory=str(settings.CHROMA_PERSIST_DIRECTORY),
        embedding_function=create_embeddings() if with_embeddings else None,
    )


def create_retriever(
    knowledge_base_id: uuid.UUID,
    *,
    k: int = 4,
) -> VectorStoreRetriever:
    if k < 1:
        raise ValueError("k must be at least 1")

    vectorstore = create_vectorstore(knowledge_base_id)

    return vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": k},
    )


def delete_document_vectors(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
) -> int:
    vectorstore = create_vectorstore(knowledge_base_id, with_embeddings=False)

    result = vectorstore.get(
        where={
            "document_id": str(document_id),
        },
        include=[],
    )

    vector_ids = result.get("ids", [])

    if not isinstance(vector_ids, list):
        raise RuntimeError("Chroma returned invalid vector IDs")

    if vector_ids:
        vectorstore.delete(ids=vector_ids)

    return len(vector_ids)


def delete_knowledge_base_vectors(
    knowledge_base_id: uuid.UUID,
) -> None:
    vectorstore = create_vectorstore(knowledge_base_id, with_embeddings=False)

    vectorstore.delete_collection()
