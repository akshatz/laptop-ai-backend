import os
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr

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
    user_id: uuid.UUID
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

class MessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True

class DocumentItem(BaseModel):
    text: str
    source: Optional[str] = None

class DocumentIngest(BaseModel):
    documents: List[DocumentItem]

class DocumentIngestResponse(BaseModel):
    documents_ingested: int
    chunks_ingested: int
