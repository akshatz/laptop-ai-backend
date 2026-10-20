import uuid
from datetime import date, datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import chat_service
import web_search
from db import get_db
from models import MessageModel, UserChatModel, UserModel
from sessions import current_user
from schemas import (
    MAX_DOCUMENTS_PER_INGEST, ChatCreate, ChatResponse, DocumentIngest, DocumentIngestResponse,
    MessageCreate, MessageResponse, WebSource,
)

router = APIRouter()

@router.get("/models")
async def list_models():
    """Chat models available in Ollama, for the /ui model dropdown."""
    try:
        models = await chat_service.list_chat_models()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama unavailable: {exc}")
    return {"default": chat_service.DEFAULT_CHAT_MODEL, "models": models}

async def _own_chat(chat_id: uuid.UUID, user: UserModel, db: AsyncSession) -> UserChatModel:
    """The chat if it belongs to the signed-in user; someone else's chat is a 404 too, so chat ids
    can't be probed."""
    query = await db.execute(
        select(UserChatModel).where(UserChatModel.id == chat_id, UserChatModel.user_id == user.id)
    )
    chat = query.scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")
    return chat

@router.get("/chats", response_model=List[ChatResponse])
async def list_chats(user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    query = await db.execute(
        select(UserChatModel).where(UserChatModel.user_id == user.id).order_by(UserChatModel.updated_at.desc())
    )
    return query.scalars().all()

@router.post("/chats", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(payload: ChatCreate, user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    new_chat = UserChatModel(
        user_id=user.id,
        title=payload.title,
        model_id=payload.model_id,
    )
    db.add(new_chat)
    await db.commit()
    await db.refresh(new_chat)

    return new_chat

@router.get("/chats/{chat_id}/messages", response_model=List[MessageResponse])
async def list_messages(chat_id: uuid.UUID, user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _own_chat(chat_id, user, db)

    query = await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )
    return query.scalars().all()

@router.post("/chats/{chat_id}/documents", response_model=DocumentIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(chat_id: uuid.UUID, payload: DocumentIngest, user: UserModel = Depends(current_user),
                          db: AsyncSession = Depends(get_db)):
    """
    Splits each given text into chunks, embeds them via Ollama, and upserts
    them into Milvus tagged with this chat_id so /messages can later retrieve
    only chunks relevant to this chat. Limited to MAX_DOCUMENTS_PER_INGEST
    documents per call to keep a single request bounded.
    """
    if not payload.documents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one document is required.")
    if len(payload.documents) > MAX_DOCUMENTS_PER_INGEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_DOCUMENTS_PER_INGEST} documents can be ingested per request.",
        )

    await _own_chat(chat_id, user, db)

    documents = chat_service.chunk_documents(chat_id, payload.documents)

    try:
        await chat_service.vector_store.aadd_documents(documents)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to ingest document into Milvus: {exc}",
        )

    return DocumentIngestResponse(documents_ingested=len(payload.documents), chunks_ingested=len(documents))

@router.post("/chats/{chat_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(chat_id: uuid.UUID, payload: MessageCreate, user: UserModel = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    """
    Persists the user's message, retrieves relevant document chunks for this
    chat from Milvus, runs the chat's history + retrieved context through a
    LangChain chain backed by ChatOllama, then persists and returns the
    assistant reply.
    """
    chat = await _own_chat(chat_id, user, db)

    query = await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )
    history = [
        AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content)
        for m in query.scalars().all()
    ]

    # Like other chat apps, a chat still called "New Chat" is named after its first question
    # (custom-frontend's titleFrom shows the same right away).
    if not history and chat.title == "New Chat":
        question = " ".join(payload.content.split())
        chat.title = question if len(question) <= 60 else question[:57].rstrip() + "…"

    user_message = MessageModel(
        chat_id=chat_id,
        user_id=chat.user_id,
        role="user",
        content=payload.content,
    )
    db.add(user_message)
    await db.commit()

    try:
        retrieved_docs = await chat_service.vector_store.asimilarity_search(
            payload.content, k=4, expr=f'chat_id == "{chat_id}"'
        )
    except Exception:
        retrieved_docs = []
    context_parts = []
    if retrieved_docs:
        context_parts.append("From the chat's documents:\n" + "\n\n".join(doc.page_content for doc in retrieved_docs))

    # Web search only when the question needs it (web_search.needs_web_search).
    web_searched = await web_search.needs_web_search(payload.content)
    web_results = await web_search.search(payload.content) if web_searched else []
    if web_results:
        context_parts.append("Web search results:\n" + web_search.as_context(web_results))
    context = "\n\n".join(context_parts) or "No relevant context found."

    chain = chat_service.build_rag_chat_chain(chat.model_id)
    try:
        ai_reply = await chain.ainvoke({
            "history": history, "input": payload.content, "context": context, "today": date.today().isoformat(),
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach the Ollama model '{chat.model_id}': {exc}",
        )

    assistant_message = MessageModel(
        chat_id=chat_id,
        user_id=chat.user_id,
        role="assistant",
        content=ai_reply.content,
    )
    db.add(assistant_message)
    chat.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(assistant_message)

    return MessageResponse.model_validate(assistant_message).model_copy(update={
        "web_searched": web_searched,
        "web_sources": [WebSource(title=r["title"], url=r["url"]) for r in web_results],
    })
