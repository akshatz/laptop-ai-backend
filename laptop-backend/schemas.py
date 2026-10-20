import os
import uuid
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, EmailStr, Field

DEFAULT_CHAT_MODEL = os.environ.get("DEFAULT_CHAT_MODEL", "llama3.2")
MAX_DOCUMENTS_PER_INGEST = 5

# 📋 AUTH SCHEMAS
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    role: str
    is_verified: bool
    mfa_enabled: bool
    is_active: bool

    class Config:
        from_attributes = True

class EmailVerifyRequest(BaseModel):
    token: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    mfa_required: bool
    password_reset_required: bool = False
    user: Optional[UserResponse] = None

class MfaOtpVerifyRequest(BaseModel):
    email: EmailStr
    otp_code: str

class PasswordChangeRequest(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str

class PasswordForgotRequest(BaseModel):
    email: EmailStr

class PasswordResetRequest(BaseModel):
    email: EmailStr
    otp_code: str
    new_password: str

# 📋 CHAT / RAG SCHEMAS
class ChatCreate(BaseModel):
    title: str = "New Chat"
    model_id: str = DEFAULT_CHAT_MODEL

class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    model_id: str
    is_pinned: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MessageCreate(BaseModel):
    content: str
    # The model picker in the message box: switch the chat to this installed model (one of
    # /models) for this question and later ones, Regenerate included.
    model: Optional[str] = None

class MessageFeedback(BaseModel):
    # 1 = like, -1 = dislike, null clears it.
    rating: Optional[Literal[1, -1]] = None
    # Optional, mainly with 👎: what was wrong. Kept only while the rating is set.
    comment: Optional[str] = Field(default=None, max_length=1000)

class FeedbackItem(BaseModel):
    message_id: uuid.UUID
    chat_id: uuid.UUID
    chat_title: str
    model_id: str
    user_name: str
    question: Optional[str] = None
    answer: str
    rating: int
    comment: Optional[str] = None
    created_at: datetime

class FeedbackModelStats(BaseModel):
    model_id: str
    answers: int
    likes: int
    dislikes: int

class FeedbackDashboard(BaseModel):
    # "everyone" for admins, "mine" otherwise.
    scope: str
    days: int
    answers: int
    likes: int
    dislikes: int
    by_model: List[FeedbackModelStats]
    recent: List[FeedbackItem]

class WebSource(BaseModel):
    title: str
    url: str

class MessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    feedback: Optional[int] = None
    feedback_comment: Optional[str] = None
    regenerations: int = 0
    # Only on the answer POST /chats/{id}/messages returns; not stored.
    web_searched: bool = False
    web_sources: List[WebSource] = []

    class Config:
        from_attributes = True

class DocumentItem(BaseModel):
    text: str
    source: Optional[str] = None

class DocumentIngest(BaseModel):
    documents: List[DocumentItem]

class ChatDocumentResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    chat_title: str
    filename: str
    content_type: str
    size_bytes: int
    chunks: int
    version: int = 1
    status: str = "ready"
    error: Optional[str] = None
    created_at: datetime
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    # False only in an admin's ?all=true list, for other people's documents.
    mine: bool = True

class UploadedDocument(BaseModel):
    id: uuid.UUID
    filename: str
    version: int
    # embedding: stored, being embedded in the background (poll /documents); duplicate: the same
    # content is already in this chat, nothing added (id is the existing document's).
    status: str

class DocumentIngestResponse(BaseModel):
    # Documents accepted for embedding (duplicates not counted).
    documents_ingested: int
    documents: List[UploadedDocument] = []

# 🛡️ ADMIN SCHEMAS
class SignedInSession(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: str
    signed_in_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    # Seen in the last 5 minutes.
    active_now: bool
    user_agent: Optional[str] = None
    is_you: bool = False
