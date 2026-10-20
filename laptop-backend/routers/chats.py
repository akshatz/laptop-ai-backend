import asyncio
import hashlib
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List
from urllib.parse import quote
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile, status
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
import chat_service
import db
import file_text
import web_search
from db import get_db
from models import ChatDocumentModel, MessageModel, UserChatModel, UserModel
from sessions import current_user
from schemas import (
    MAX_DOCUMENTS_PER_INGEST, ChatCreate, ChatDocumentResponse, ChatResponse, DocumentIngest, DocumentIngestResponse,
    DocumentItem, FeedbackDashboard, UploadedDocument, FeedbackItem, FeedbackModelStats, MessageCreate, MessageFeedback, MessageResponse, WebSource,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Questions allowed per chat; 0 (default) = no limit. 1 makes every question a new chat, so small
# models can't mix in earlier topics, at the cost of follow-up questions.
MAX_QUESTIONS_PER_CHAT = int(os.environ.get("MAX_QUESTIONS_PER_CHAT", "0"))
# Factual questions (the WEB/NO decision says WEB) are answered only from the chat's documents and
# web results, and with nothing found the model isn't asked at all (NO_SOURCES_REPLY). Requests
# that need no facts from the web (recipes, writing, translation, maths, greetings) still use the
# model's own knowledge. false = factual answers may draw on the model's knowledge too.
ANSWER_FROM_SOURCES_ONLY = os.environ.get("ANSWER_FROM_SOURCES_ONLY", "true").lower() not in ("false", "0", "no")
NO_SOURCES_REPLY = "I couldn't find this in your documents or on the web."
# Regenerations allowed per answer (2: first answer + 2 more, like Open WebUI's Regenerate Limit
# Function); 0 = no limit.
MAX_REGENERATIONS = int(os.environ.get("MAX_REGENERATIONS", "2"))

@router.get("/models")
async def list_models():
    """Chat models available in Ollama, for the /ui model dropdown."""
    try:
        models = await chat_service.list_chat_models()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama unavailable: {exc}")
    return {"default": chat_service.DEFAULT_CHAT_MODEL, "models": models,
            "max_questions_per_chat": MAX_QUESTIONS_PER_CHAT, "max_regenerations": MAX_REGENERATIONS}

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

@router.put("/chats/{chat_id}/messages/{message_id}/feedback", response_model=MessageResponse)
async def rate_message(chat_id: uuid.UUID, message_id: uuid.UUID, payload: MessageFeedback,
                       user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Like/dislike an answer in one of the user's own chats (questions can't be rated). The first
    rating is final: changing or clearing it is a 409 (Regenerate gives a new, unrated answer)."""
    await _own_chat(chat_id, user, db)
    query = await db.execute(
        select(MessageModel).where(MessageModel.id == message_id, MessageModel.chat_id == chat_id,
                                   MessageModel.role == "assistant")
    )
    message = query.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found.")
    if message.feedback is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This answer is already rated.")
    if payload.rating is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose 👍 or 👎.")
    message.feedback = payload.rating
    message.feedback_comment = (payload.comment or "").strip() or None
    await db.commit()
    await db.refresh(message)
    return message

@router.get("/feedback", response_model=FeedbackDashboard)
async def feedback_dashboard(days: int = 30, user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Likes/dislikes on answers over the last `days` days: totals, per model and the latest rated
    answers with their question. Admins (users.role == "admin") see everyone's, others their own."""
    days = max(1, min(days, 365))
    everyone = user.role == "admin"
    since = datetime.now(timezone.utc) - timedelta(days=days)
    scope = [MessageModel.role == "assistant", MessageModel.created_at >= since]
    if not everyone:
        scope.append(MessageModel.user_id == user.id)

    likes = func.count(case((MessageModel.feedback == 1, 1)))
    dislikes = func.count(case((MessageModel.feedback == -1, 1)))
    rows = (await db.execute(
        select(UserChatModel.model_id, func.count(), likes, dislikes)
        .join(UserChatModel, UserChatModel.id == MessageModel.chat_id)
        .where(*scope).group_by(UserChatModel.model_id).order_by(func.count().desc())
    )).all()
    by_model = [FeedbackModelStats(model_id=m, answers=a, likes=l, dislikes=d) for m, a, l, d in rows]

    # The question an answer replied to: the latest user message before it in the same chat.
    Question = aliased(MessageModel)
    question = (
        select(Question.content)
        .where(Question.chat_id == MessageModel.chat_id, Question.role == "user",
               Question.created_at <= MessageModel.created_at)
        .order_by(Question.created_at.desc()).limit(1).correlate(MessageModel).scalar_subquery()
    )
    recent = (await db.execute(
        select(MessageModel, UserChatModel.title, UserChatModel.model_id, UserModel.name, question)
        .join(UserChatModel, UserChatModel.id == MessageModel.chat_id)
        .join(UserModel, UserModel.id == UserChatModel.user_id)
        .where(*scope, MessageModel.feedback.is_not(None))
        .order_by(MessageModel.created_at.desc()).limit(100)
    )).all()

    return FeedbackDashboard(
        scope="everyone" if everyone else "mine",
        days=days,
        answers=sum(s.answers for s in by_model),
        likes=sum(s.likes for s in by_model),
        dislikes=sum(s.dislikes for s in by_model),
        by_model=by_model,
        recent=[FeedbackItem(message_id=m.id, chat_id=m.chat_id, chat_title=title, model_id=model_id,
                             user_name=name, question=q, answer=m.content, rating=m.feedback,
                             comment=m.feedback_comment,
                             created_at=m.created_at)
                for m, title, model_id, name, q in recent],
    )

@router.post("/chats/{chat_id}/documents", response_model=DocumentIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(chat_id: uuid.UUID, payload: DocumentIngest, background: BackgroundTasks,
                          user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
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

    files = [
        (item, (item.source or f"Pasted text {i}.txt"), "text/plain; charset=utf-8", item.text.encode())
        for i, item in enumerate(payload.documents, start=1)
    ]
    # Pasted texts get generated names, so only identical content counts as the same document.
    return await _ingest(chat_id, user, files, db, background, versioned=False)

async def _ingest(chat_id: uuid.UUID, user: UserModel, files: list, db: AsyncSession,
                  background: BackgroundTasks, versioned: bool = True) -> DocumentIngestResponse:
    """Stores each (item, filename, content_type, original bytes) in chat_documents as `embedding`
    and returns at once; _embed then adds the chunks to Milvus (collection
    chat_service.MILVUS_COLLECTION, tagged with the chat id) in the background, and the client
    polls /documents for the status. Content already in this chat (same sha256, not failed or
    replaced) is a duplicate and isn't added again. With `versioned`, a file whose name is already
    in the chat with other content is its next version and replaces that one's chunks."""
    existing = (await db.execute(
        select(ChatDocumentModel).where(ChatDocumentModel.chat_id == chat_id,
                                        ChatDocumentModel.status.in_(("embedding", "ready")))
    )).scalars().all()
    by_hash = {d.sha256: d for d in existing if d.sha256}
    latest_version: dict[str, int] = {}
    for d in (await db.execute(
        select(ChatDocumentModel.filename, func.max(ChatDocumentModel.version))
        .where(ChatDocumentModel.chat_id == chat_id).group_by(ChatDocumentModel.filename)
    )).all():
        latest_version[d[0]] = d[1]

    results, jobs = [], []
    for item, filename, content_type, data in files:
        filename = filename[:255]
        digest = hashlib.sha256(data).hexdigest()
        same = by_hash.get(digest)
        if same:
            results.append(UploadedDocument(id=same.id, filename=same.filename, version=same.version, status="duplicate"))
            continue
        version = latest_version.get(filename, 0) + 1 if versioned else 1
        row = ChatDocumentModel(
            chat_id=chat_id, user_id=user.id, filename=filename, content_type=content_type[:100],
            size_bytes=len(data), chunks=0, data=data, sha256=digest, version=version, status="embedding",
        )
        db.add(row)
        by_hash[digest] = row
        latest_version[filename] = version
        jobs.append((row, item))
    await db.commit()

    for row, item in jobs:
        results.append(UploadedDocument(id=row.id, filename=row.filename, version=row.version, status="embedding"))
    background.add_task(_embed, chat_id, [(row.id, row.filename, row.version, item) for row, item in jobs], versioned)
    return DocumentIngestResponse(documents_ingested=len(jobs), documents=results)

def _milvus_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

async def _embed(chat_id: uuid.UUID, jobs: list, versioned: bool) -> None:
    """Background part of _ingest, with its own DB session (the request's is closed by now). A new
    version first removes the chunks Milvus holds for that filename in this chat (chunks carry only
    chat_id and source), then adds its own; older rows become `replaced`. A failure marks the
    document `failed` with the error and leaves the others alone."""
    async with db.AsyncSessionLocal() as session:
        for doc_id, filename, version, item in jobs:
            try:
                if versioned and version > 1:
                    expr = f"chat_id == {_milvus_str(str(chat_id))} and source == {_milvus_str(filename)}"
                    await asyncio.to_thread(chat_service.vector_store.delete, expr=expr)
                chunks = chat_service.chunk_documents(chat_id, [item])
                await chat_service.vector_store.aadd_documents(chunks)
            except Exception as exc:
                log.warning("Embedding %s (chat %s) failed: %s", filename, chat_id, exc)
                await session.execute(update(ChatDocumentModel).where(ChatDocumentModel.id == doc_id)
                                      .values(status="failed", error=str(exc)[:500]))
                await session.commit()
                continue
            await session.execute(update(ChatDocumentModel).where(ChatDocumentModel.id == doc_id)
                                  .values(status="ready", chunks=len(chunks), error=None))
            if versioned and version > 1:
                await session.execute(
                    update(ChatDocumentModel)
                    .where(ChatDocumentModel.chat_id == chat_id, ChatDocumentModel.filename == filename,
                           ChatDocumentModel.version < version, ChatDocumentModel.status == "ready")
                    .values(status="replaced")
                )
            await session.commit()

MAX_FILE_BYTES = 10 * 1024 * 1024

@router.post("/chats/{chat_id}/files", response_model=DocumentIngestResponse, status_code=status.HTTP_201_CREATED)
async def upload_files(chat_id: uuid.UUID, background: BackgroundTasks, files: list[UploadFile] = File(...),
                       user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Same as /documents, from uploaded files (PDF, .docx, text; see file_text.py): up to
    MAX_DOCUMENTS_PER_INGEST files of at most 10 MB each. Every file is checked before any is
    added, so one bad file adds nothing."""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one file is required.")
    if len(files) > MAX_DOCUMENTS_PER_INGEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_DOCUMENTS_PER_INGEST} files can be added at once.",
        )
    await _own_chat(chat_id, user, db)

    items = []
    for upload in files:
        name = upload.filename or "file"
        data = await upload.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{name}: larger than 10 MB.")
        try:
            item = DocumentItem(text=file_text.extract_text(name, data), source=name)
        except file_text.UnsupportedFile as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        items.append((item, name, upload.content_type or "application/octet-stream", data))

    return await _ingest(chat_id, user, items, db, background)

@router.get("/documents", response_model=List[ChatDocumentResponse])
async def list_documents(all: bool = False, user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Your own documents across all your chats, newest first (the file contents aren't sent).
    `?all=true` lists everyone's, with the owner, for admins (users.role == "admin") only."""
    if all and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can see everyone's documents.")
    query = (
        select(ChatDocumentModel, UserChatModel.title, UserModel.name, UserModel.email)
        .join(UserChatModel, UserChatModel.id == ChatDocumentModel.chat_id)
        .join(UserModel, UserModel.id == ChatDocumentModel.user_id)
        .order_by(ChatDocumentModel.created_at.desc())
    )
    if not all:
        query = query.where(ChatDocumentModel.user_id == user.id)
    return [
        ChatDocumentResponse(
            id=d.id, chat_id=d.chat_id, chat_title=title, filename=d.filename, content_type=d.content_type,
            size_bytes=d.size_bytes, chunks=d.chunks, version=d.version, status=d.status, error=d.error,
            created_at=d.created_at, owner_name=name, owner_email=email, mine=d.user_id == user.id,
        )
        for d, title, name, email in (await db.execute(query)).all()
    ]

@router.get("/documents/{document_id}/file")
async def open_document(document_id: uuid.UUID, user: UserModel = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The original file, shown in the browser where it can (PDF, text). Someone else's document is
    a 404, except for admins (they see everyone's in the documents library)."""
    query = select(ChatDocumentModel).where(ChatDocumentModel.id == document_id)
    if user.role != "admin":
        query = query.where(ChatDocumentModel.user_id == user.id)
    query = await db.execute(query)
    doc = query.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    # Served with its own type, but never as HTML/SVG (scripts would run on this origin).
    safe_type = doc.content_type
    if any(t in safe_type for t in ("html", "svg", "xml", "javascript")):
        safe_type = "text/plain; charset=utf-8"
    return Response(
        content=doc.data, media_type=safe_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )

async def _generate(chat: UserChatModel, question: str, history: list,
                    rejected: tuple[str, str | None] | None = None) -> tuple[str, bool, list[dict]]:
    """One answer to `question`: the chat's document chunks from Milvus, web results when the
    question needs them (web_search.needs_web_search), then the chat's model with `history`
    (sources-only prompt for those factual questions when ANSWER_FROM_SOURCES_ONLY).
    `rejected` = (previous answer, the user's comment) when regenerating an answer rated 👎: both go
    into the context so the new answer doesn't repeat it.
    Returns (answer, web_searched, web_results); 502 when Ollama can't be reached."""
    try:
        retrieved_docs = await chat_service.vector_store.asimilarity_search(
            question, k=4, expr=f'chat_id == "{chat.id}"'
        )
    except Exception:
        retrieved_docs = []
    context_parts = []
    if retrieved_docs:
        context_parts.append("From the chat's documents:\n" + "\n\n".join(doc.page_content for doc in retrieved_docs))

    # The WEB/NO decision (web_search.needs_web_search) says whether the question is about facts
    # that need the web. With ANSWER_FROM_SOURCES_ONLY, a WEB question is answered only from the
    # sources (nothing found → NO_SOURCES_REPLY without asking the model); a NO request (recipes,
    # writing, translation, maths, greetings) may use the model's own knowledge.
    web_searched = await web_search.needs_web_search(question)
    web_results = await web_search.search(question) if web_searched else []
    if web_results:
        context_parts.append("Web search results:\n" + web_search.as_context(web_results))
    strict = ANSWER_FROM_SOURCES_ONLY and web_searched
    if strict and not context_parts:
        return NO_SOURCES_REPLY, web_searched, []
    context = "\n\n".join(context_parts) or "No relevant context found."
    if rejected:
        previous, comment = rejected
        context += (
            "\n\nThe user marked this earlier answer to the same question as bad"
            + (f" and said: \"{comment}\"" if comment else "")
            + ". Don't repeat its mistakes; check the sources above again.\nEarlier answer:\n" + previous
        )

    chain = chat_service.build_rag_chat_chain(chat.model_id, sources_only=strict)
    try:
        ai_reply = await chain.ainvoke({
            "history": history, "input": question, "context": context, "today": date.today().isoformat(),
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach the Ollama model '{chat.model_id}': {exc}",
        )

    return ai_reply.content, web_searched, web_results

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
    if payload.model and chat_service._with_tag(payload.model) != chat_service._with_tag(chat.model_id):
        try:
            installed = await chat_service.list_chat_models()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama unavailable: {exc}")
        if chat_service._with_tag(payload.model) not in installed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown model '{payload.model}'.")
        chat.model_id = chat_service._with_tag(payload.model)

    query = await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )
    history = [
        AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content)
        for m in query.scalars().all()
    ]
    if MAX_QUESTIONS_PER_CHAT and sum(isinstance(m, HumanMessage) for m in history) >= MAX_QUESTIONS_PER_CHAT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This chat already has its question. Start a new chat for another one.",
        )

    # Like other chat apps, a chat still called "New Chat" is named after its first question
    # (laptop-frontend's titleFrom shows the same right away).
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

    content, web_searched, web_results = await _generate(chat, payload.content, history)

    assistant_message = MessageModel(
        chat_id=chat_id,
        user_id=chat.user_id,
        role="assistant",
        content=content,
    )
    db.add(assistant_message)
    chat.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(assistant_message)

    return MessageResponse.model_validate(assistant_message).model_copy(update={
        "web_searched": web_searched,
        "web_sources": [WebSource(title=r["title"], url=r["url"]) for r in web_results],
    })

@router.post("/chats/{chat_id}/messages/{message_id}/regenerate", response_model=MessageResponse)
async def regenerate_answer(chat_id: uuid.UUID, message_id: uuid.UUID, user: UserModel = Depends(current_user),
                            db: AsyncSession = Depends(get_db)):
    """A new answer to the question before the chat's latest answer, replacing it in place (its
    rating is cleared, `regenerations` counts up). Only the latest answer can be regenerated, at
    most MAX_REGENERATIONS times (409 after that)."""
    chat = await _own_chat(chat_id, user, db)
    messages = (await db.execute(
        select(MessageModel).where(MessageModel.chat_id == chat_id).order_by(MessageModel.created_at)
    )).scalars().all()
    if not messages or messages[-1].id != message_id or messages[-1].role != "assistant":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Only the latest answer can be regenerated.")
    answer = messages[-1]
    if MAX_REGENERATIONS and answer.regenerations >= MAX_REGENERATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This answer was already regenerated {answer.regenerations} times.",
        )
    question_index = max((i for i, m in enumerate(messages[:-1]) if m.role == "user"), default=None)
    if question_index is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No question to answer.")
    history = [
        AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content)
        for m in messages[:question_index]
    ]

    # A 👎 (and its comment) tells the new answer what was wrong with this one.
    rejected = (answer.content, answer.feedback_comment) if answer.feedback == -1 else None
    content, web_searched, web_results = await _generate(chat, messages[question_index].content, history, rejected)

    answer.content = content
    answer.feedback = None
    answer.feedback_comment = None
    answer.regenerations += 1
    chat.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(answer)
    return MessageResponse.model_validate(answer).model_copy(update={
        "web_searched": web_searched,
        "web_sources": [WebSource(title=r["title"], url=r["url"]) for r in web_results],
    })
