import { useEffect, useState, type FormEvent } from 'react';
import { api, type Chat, type User } from '../api';

interface Props {
  user: User;
  onUserChange: (user: User) => void;
  onSignOut: () => void;
}

const MAX_DOCS = 5;

export default function ChatScreen({ user, onUserChange, onSignOut }: Props) {
  const [status, setStatus] = useState('');
  const [models, setModels] = useState<string[]>(['llama3.2']);
  const [model, setModel] = useState('llama3.2');
  const [chat, setChat] = useState<Chat | null>(null);
  const [docs, setDocs] = useState<string[]>(Array(MAX_DOCS).fill(''));
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [mfaPassword, setMfaPassword] = useState('');

  // Fill the model list from Ollama; on failure llama3.2 stays.
  useEffect(() => {
    api.models().then(({ default: def, models }) => {
      if (!models.length) return;
      const wanted = def.includes(':') ? def : def + ':latest';
      setModels(models);
      setModel(models.includes(wanted) ? wanted : models[0]);
    }).catch(err => setStatus('Could not load the model list: ' + err.message));
  }, []);

  async function createChat() {
    setStatus('');
    try {
      setChat(await api.createChat(user.id, model));
      setMessages([]);
    } catch (err) {
      setStatus('Failed to create chat: ' + (err as Error).message);
    }
  }

  async function ingest() {
    if (!chat) return;
    const texts = docs.map(d => d.trim()).filter(Boolean);
    if (!texts.length) return;
    setStatus('');
    try {
      const r = await api.ingest(chat.id, texts);
      setStatus(`Ingested ${r.documents_ingested} document(s), ${r.chunks_ingested} chunk(s).`);
    } catch (err) {
      setStatus('Failed to ingest documents: ' + (err as Error).message);
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!chat || !content) return;
    setStatus('');
    setInput('');
    setMessages(m => [...m, { role: 'user', content }]);
    setSending(true);
    try {
      const reply = await api.send(chat.id, content);
      setMessages(m => [...m, { role: 'assistant', content: reply.content }]);
    } catch (err) {
      setStatus('Failed to send message: ' + (err as Error).message);
    } finally {
      setSending(false);
    }
  }

  // /mfa/enable and /mfa/disable check the password again.
  async function toggleMfa(e: FormEvent) {
    e.preventDefault();
    setStatus('');
    try {
      const updated = await api.setMfa(!user.mfa_enabled, user.email, mfaPassword);
      setMfaPassword('');
      onUserChange(updated);
      setStatus(updated.mfa_enabled
        ? 'MFA is on: signing in now also asks for a code sent by email.'
        : 'MFA is off: signing in needs only your password.');
    } catch (err) {
      setStatus('Changing MFA failed: ' + (err as Error).message);
    }
  }

  return (
    <>
      <div className="account">
        <span>Signed in as <strong>{user.name}</strong> &lt;{user.email}&gt;</span>
        <button type="button" onClick={onSignOut}>Sign out</button>
        <details>
          <summary>Account</summary>
          <form onSubmit={toggleMfa}>
            <p>Email code at sign-in (MFA): <strong>{user.mfa_enabled ? 'on' : 'off'}</strong></p>
            <label>Password (needed to change MFA)
              <input type="password" autoComplete="current-password" required value={mfaPassword}
                onChange={e => setMfaPassword(e.target.value)} />
            </label>
            <button type="submit">{user.mfa_enabled ? 'Turn MFA off' : 'Turn MFA on'}</button>
          </form>
        </details>
      </div>

      {status && <p className="status">{status}</p>}

      <section className="card">
        <h2>1. Create a chat</h2>
        <label>Model
          <select value={model} onChange={e => setModel(e.target.value)}>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <button type="button" onClick={createChat}>{chat ? 'New chat' : 'Create chat'}</button>
        <p>Chat ID: <code>{chat ? chat.id : '(none yet)'}</code></p>
      </section>

      {chat && (
        <>
          <details className="card">
            <summary>2. Add documents for context (optional, up to {MAX_DOCS})</summary>
            {docs.map((d, i) => (
              <label key={i}>Text {i + 1}
                <textarea value={d} onChange={e => setDocs(ds => ds.map((x, j) => (j === i ? e.target.value : x)))} />
              </label>
            ))}
            <button type="button" onClick={ingest}>Ingest</button>
          </details>

          <section className="card">
            <h2>3. Chat</h2>
            <div className="messages">
              {messages.map((m, i) => (
                <div key={i} className={'msg ' + m.role}><span className="role">{m.role}:</span> {m.content}</div>
              ))}
              {sending && <div className="msg assistant"><span className="role">assistant:</span> …</div>}
            </div>
            <form onSubmit={send} className="send">
              <input placeholder="Say something" value={input} onChange={e => setInput(e.target.value)} />
              <button type="submit" disabled={sending}>Send</button>
            </form>
          </section>
        </>
      )}
    </>
  );
}
