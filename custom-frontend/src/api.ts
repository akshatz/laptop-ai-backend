// Every backend call goes through /api, which nginx (container) or Vite (dev) forwards to
// custom-backend with the prefix stripped.
const BASE = '/api';

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  is_verified: boolean;
  mfa_enabled: boolean;
}

export interface LoginResult {
  mfa_required: boolean;
  password_reset_required: boolean;
  user: User | null;
}

export interface Chat {
  id: string;
  title: string;
  model_id: string;
}

export interface Message {
  id: string;
  role: string;
  content: string;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  login: (email: string, password: string) => request<LoginResult>('/login', { email, password }),
  verifyOtp: (email: string, otp_code: string) => request<User>('/mfa/verify-otp', { email, otp_code }),
  signUp: (name: string, email: string, password: string) => request<User>('/users', { name, email, password }),
  verifyEmail: (token: string) => request<User>('/users/verify', { token }),
  setMfa: (on: boolean, email: string, password: string) =>
    request<User>(on ? '/mfa/enable' : '/mfa/disable', { email, password }),
  models: () => request<{ default: string; models: string[] }>('/models'),
  createChat: (user_id: string, model_id: string) => request<Chat>('/chats', { user_id, model_id }),
  ingest: (chatId: string, texts: string[]) =>
    request<{ documents_ingested: number; chunks_ingested: number }>(
      `/chats/${chatId}/documents`, { documents: texts.map(text => ({ text })) }),
  send: (chatId: string, content: string) => request<Message>(`/chats/${chatId}/messages`, { content }),
};
