import os
import re
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_milvus import Milvus
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from db import secrets

# 🦜 LangChain chat setup: talks to the ollama container via its internal
# Docker DNS name. Model is chosen per-request (client picks a model already
# pulled into the ollama container); this is just the fallback default.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
DEFAULT_CHAT_MODEL = os.environ.get("DEFAULT_CHAT_MODEL", "llama3.2")

RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Use the context below to answer, if relevant.\n\nContext:\n{context}"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

def build_rag_chat_chain(model: str):
    llm = ChatOllama(base_url=OLLAMA_BASE_URL, model=model)
    return RAG_PROMPT | llm

# 🧬 Milvus vector store for chat document retrieval (RAG), same instance
# Open WebUI uses for its own document uploads, but a separate collection so
# the two don't collide. Embeddings use the same Ollama model Open WebUI is
# configured with (see docker-compose.yml's RAG_EMBEDDING_MODEL).
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "embeddinggemma")
# One collection per embedding model: vectors from different models aren't
# comparable (even at the same dimension), so switching models starts a fresh
# collection instead of silently mixing them. Re-ingest documents after a switch.
MILVUS_COLLECTION = "custom_backend_docs_" + re.sub(r"\W", "_", EMBEDDING_MODEL.split(":")[0])

# embeddinggemma is trained with task prefixes on queries vs. documents; they
# measurably improve retrieval. Other models get no prefix. Keep in sync with
# RAG_EMBEDDING_QUERY_PREFIX / RAG_EMBEDDING_CONTENT_PREFIX in docker-compose.yml.
_PREFIXES = {
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
}
QUERY_PREFIX, DOCUMENT_PREFIX = _PREFIXES.get(EMBEDDING_MODEL.split(":")[0], ("", ""))


class PrefixedOllamaEmbeddings(OllamaEmbeddings):
    """OllamaEmbeddings that prepends the model's query/document prefixes."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([DOCUMENT_PREFIX + t for t in texts])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await super().aembed_documents([DOCUMENT_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return super().embed_documents([QUERY_PREFIX + text])[0]

    async def aembed_query(self, text: str) -> list[float]:
        return (await super().aembed_documents([QUERY_PREFIX + text]))[0]


_embeddings = PrefixedOllamaEmbeddings(base_url=OLLAMA_BASE_URL, model=EMBEDDING_MODEL)
vector_store = Milvus(
    embedding_function=_embeddings,
    collection_name=MILVUS_COLLECTION,
    connection_args={
        "uri": os.environ["MILVUS_URI"],
        "token": f"root:{secrets['milvus_root_password']}",
    },
    auto_id=True,
)
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

def chunk_documents(chat_id, items) -> list[Document]:
    """items: iterable of objects with .text and .source (schemas.DocumentItem)."""
    return [
        Document(page_content=chunk, metadata={"chat_id": str(chat_id), "source": item.source})
        for item in items
        for chunk in text_splitter.split_text(item.text)
    ]
