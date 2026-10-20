import os
import re
import httpx
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
# Same as Open WebUI's DEFAULT_MODEL_PARAMS temperature (docker-compose.yml).
CHAT_TEMPERATURE = float(os.environ.get("CHAT_TEMPERATURE", "0.1"))

RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Today is {today}. Use the context below to answer, if relevant. "
               "Take facts, numbers, names and dates about news or current events only from the context; "
               "if the context doesn't cover the question, say plainly that you found no current "
               "information on it instead of answering from memory, and don't invent events. "
               "When you use a numbered web result, cite it like [1].\n\nContext:\n{context}"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

# ANSWER_FROM_SOURCES_ONLY (routers/chats.py): nothing from the model's own knowledge.
SOURCES_ONLY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Today is {today}. Answer ONLY with information found in the "
               "context below, which comes from the user's uploaded documents and a web search. Do not "
               "use your own knowledge, and never guess or invent facts, numbers, names or dates. If the "
               "context doesn't contain the answer, reply exactly: \"I couldn't find this in your "
               "documents or on the web.\" When you use a numbered web result, cite it like [1].\n\n"
               "Context:\n{context}"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

def build_rag_chat_chain(model: str, sources_only: bool = False):
    llm = ChatOllama(base_url=OLLAMA_BASE_URL, model=model, temperature=CHAT_TEMPERATURE)
    return (SOURCES_ONLY_PROMPT if sources_only else RAG_PROMPT) | llm

# Models offered in /ui's dropdown, comma-separated; a name without a tag means `:latest`.
# Only chat models that fit in RAM: not embedding models, photo-ai (pictures; /ui sends text
# only), codegemma (code) or gemma3:12b/smart-ai (~8 GB, don't fit next to the stack).
CHAT_MODELS = os.environ.get("CHAT_MODELS", "llama3.2,fast-ai,gemma3:4b")

def _with_tag(name: str) -> str:
    return name if ":" in name else name + ":latest"

async def list_chat_models() -> list[str]:
    """The CHAT_MODELS that are pulled into Ollama, in CHAT_MODELS order."""
    async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=5) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
    installed = {m["name"] for m in response.json().get("models", [])}
    wanted = [_with_tag(n.strip()) for n in CHAT_MODELS.split(",") if n.strip()]
    return [n for n in wanted if n in installed]

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
