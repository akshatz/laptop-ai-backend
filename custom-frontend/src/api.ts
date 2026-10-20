// Every backend call goes through /api, which nginx (container) or Vite (dev) forwards to
// custom-backend with the prefix stripped. The session is an HttpOnly cookie set by the backend
// after authentik sign-in; same-origin fetches send it automatically.
const BASE = '/api';

export const SIGN_IN_URL = `${BASE}/auth/login`;

export interface User {
  id: string;
  email: string;
  name: string;
  // "admin" sees everyone's documents and feedback.
  role?: string;
}

export interface Chat {
  id: string;
  title: string;
  model_id: string;
  created_at: string;
}

export interface WebSource {
  title: string;
  url: string;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  web_searched?: boolean;
  web_sources?: WebSource[];
  // Answers only: 1 = like, -1 = dislike, null = not rated.
  feedback?: Feedback;
  // Answers only: how often this answer was regenerated.
  regenerations?: number;
}

export type Feedback = 1 | -1 | null;

export interface FeedbackItem {
  message_id: string;
  chat_id: string;
  chat_title: string;
  model_id: string;
  user_name: string;
  question: string | null;
  answer: string;
  rating: 1 | -1;
  created_at: string;
}

export interface FeedbackDashboard {
  scope: 'everyone' | 'mine';
  days: number;
  answers: number;
  likes: number;
  dislikes: number;
  by_model: { model_id: string; answers: number; likes: number; dislikes: number }[];
  recent: FeedbackItem[];
}

export interface ChatDocument {
  id: string;
  chat_id: string;
  chat_title: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  chunks: number;
  version: number;
  // embedding → ready | failed; an older version of the same file becomes replaced.
  status: 'embedding' | 'ready' | 'failed' | 'replaced';
  error: string | null;
  created_at: string;
  owner_name?: string | null;
  owner_email?: string | null;
  // False only in an admin's everyone list, for other people's documents.
  mine?: boolean;
}

export interface UploadedDocument {
  id: string;
  filename: string;
  version: number;
  status: 'embedding' | 'duplicate';
}

export interface SignedInSession {
  id: string;
  user_id: string;
  name: string;
  email: string;
  role: string;
  signed_in_at: string;
  last_seen_at: string;
  expires_at: string;
  // Seen in the last 5 minutes.
  active_now: boolean;
  user_agent: string | null;
  is_you: boolean;
}

export interface IngestResult {
  documents_ingested: number;
  documents: UploadedDocument[];
}

export const documentUrl = (id: string) => `${BASE}/documents/${id}/file`;

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
  // FormData (file uploads) goes as multipart; the browser sets its Content-Type.
  const isForm = body instanceof FormData;
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* not JSON */ }
    throw new HttpError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  me: () => request<User>('/auth/me'),
  logout: () => request<{ authentik_logout_url: string | null }>('/auth/logout', undefined, 'POST'),
  models: () => request<{ default: string; models: string[]; max_questions_per_chat?: number; max_regenerations?: number }>('/models'),
  chats: () => request<Chat[]>('/chats'),
  createChat: (model_id: string) => request<Chat>('/chats', { model_id }),
  messages: (chatId: string) => request<Message[]>(`/chats/${chatId}/messages`),
  ingest: (chatId: string, texts: string[]) =>
    request<IngestResult>(
      `/chats/${chatId}/documents`, { documents: texts.map(text => ({ text })) }),
  upload: (chatId: string, files: File[]) => {
    const form = new FormData();
    files.forEach(f => form.append('files', f));
    return request<IngestResult>(`/chats/${chatId}/files`, form);
  },
  adminSessions: () => request<SignedInSession[]>('/admin/sessions'),
  documents: (all = false) => request<ChatDocument[]>(all ? '/documents?all=true' : '/documents'),
  send: (chatId: string, content: string) => request<Message>(`/chats/${chatId}/messages`, { content }),
  feedback: (days: number) => request<FeedbackDashboard>(`/feedback?days=${days}`),
  regenerate: (chatId: string, messageId: string) =>
    request<Message>(`/chats/${chatId}/messages/${messageId}/regenerate`, undefined, 'POST'),
  rate: (chatId: string, messageId: string, rating: Feedback) =>
    request<Message>(`/chats/${chatId}/messages/${messageId}/feedback`, { rating }, 'PUT'),
};
