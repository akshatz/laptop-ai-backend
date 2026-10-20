// Every backend call goes through /api, which nginx (container) or Vite (dev) forwards to
// custom-backend with the prefix stripped. The session is an HttpOnly cookie set by the backend
// after authentik sign-in; same-origin fetches send it automatically.
const BASE = '/api';

export const SIGN_IN_URL = `${BASE}/auth/login`;

export interface User {
  id: string;
  email: string;
  name: string;
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
}

export interface ChatDocument {
  id: string;
  chat_id: string;
  chat_title: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  chunks: number;
  created_at: string;
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
  models: () => request<{ default: string; models: string[] }>('/models'),
  chats: () => request<Chat[]>('/chats'),
  createChat: (model_id: string) => request<Chat>('/chats', { model_id }),
  messages: (chatId: string) => request<Message[]>(`/chats/${chatId}/messages`),
  ingest: (chatId: string, texts: string[]) =>
    request<{ documents_ingested: number; chunks_ingested: number }>(
      `/chats/${chatId}/documents`, { documents: texts.map(text => ({ text })) }),
  upload: (chatId: string, files: File[]) => {
    const form = new FormData();
    files.forEach(f => form.append('files', f));
    return request<{ documents_ingested: number; chunks_ingested: number }>(`/chats/${chatId}/files`, form);
  },
  documents: () => request<ChatDocument[]>('/documents'),
  send: (chatId: string, content: string) => request<Message>(`/chats/${chatId}/messages`, { content }),
};
