import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import chat_service
from db import get_db
from models import MessageModel, UserChatModel, UserModel
from schemas import (
    MAX_DOCUMENTS_PER_INGEST, ChatCreate, ChatResponse, DocumentIngest, DocumentIngestResponse,
    MessageCreate, MessageResponse,
)

router = APIRouter()

@router.post("/chats", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(payload: ChatCreate, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.id == payload.user_id))
    if not query.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    new_chat = UserChatModel(
        user_id=payload.user_id,
        title=payload.title,
        model_id=payload.model_id,
    )
    db.add(new_chat)
    await db.commit()
    await db.refresh(new_chat)

    return new_chat

@router.get("/chats/{chat_id}/messages", response_model=List[MessageResponse])
async def list_messages(chat_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserChatModel).where(UserChatModel.id == chat_id))
    if not query.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    query = await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )
    return query.scalars().all()

@router.post("/chats/{chat_id}/documents", response_model=DocumentIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(chat_id: uuid.UUID, payload: DocumentIngest, db: AsyncSession = Depends(get_db)):
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

    query = await db.execute(select(UserChatModel).where(UserChatModel.id == chat_id))
    if not query.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

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
async def send_message(chat_id: uuid.UUID, payload: MessageCreate, db: AsyncSession = Depends(get_db)):
    """
    Persists the user's message, retrieves relevant document chunks for this
    chat from Milvus, runs the chat's history + retrieved context through a
    LangChain chain backed by ChatOllama, then persists and returns the
    assistant reply.
    """
    query = await db.execute(select(UserChatModel).where(UserChatModel.id == chat_id))
    chat = query.scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    query = await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )
    history = [
        AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content)
        for m in query.scalars().all()
    ]

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
    context = "\n\n".join(doc.page_content for doc in retrieved_docs) or "No relevant context found."

    chain = chat_service.build_rag_chat_chain(chat.model_id)
    try:
        ai_reply = await chain.ainvoke({"history": history, "input": payload.content, "context": context})
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

    return assistant_message
