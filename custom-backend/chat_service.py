import os
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
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
MILVUS_COLLECTION = "custom_backend_docs"

_embeddings = OllamaEmbeddings(base_url=OLLAMA_BASE_URL, model=EMBEDDING_MODEL)
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
