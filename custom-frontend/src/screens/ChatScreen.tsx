import { useEffect, useState, type FormEvent } from 'react';
import { api, HttpError, type Chat, type User } from '../api';

interface Props {
  user: User;
  onSignOut: () => void;
  onSessionExpired: () => void;
}

const MAX_DOCS = 5;

export default function ChatScreen({ user, onSignOut, onSessionExpired }: Props) {
  const [status, setStatus] = useState('');
  const [models, setModels] = useState<string[]>(['llama3.2']);
  const [model, setModel] = useState('llama3.2');
  const [chats, setChats] = useState<Chat[]>([]);
  const [chat, setChat] = useState<Chat | null>(null);
  const [docs, setDocs] = useState<string[]>(Array(MAX_DOCS).fill(''));
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);

  function fail(prefix: string, err: unknown) {
    if (err instanceof HttpError && err.status === 401) { onSessionExpired(); return; }
    setStatus(prefix + (err as Error).message);
  }

  useEffect(() => {
    // Fill the model list from Ollama; on failure llama3.2 stays.
    api.models().then(({ default: def, models }) => {
      if (!models.length) return;
      const wanted = def.includes(':') ? def : def + ':latest';
      setModels(models);
      setModel(models.includes(wanted) ? wanted : models[0]);
    }).catch(err => fail('Could not load the model list: ', err));
    api.chats().then(setChats).catch(err => fail('Could not load your chats: ', err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function newChat() {
    setStatus('');
    try {
      const created = await api.createChat(model);
      setChats(cs => [created, ...cs]);
      setChat(created);
      setMessages([]);
    } catch (err) {
      fail('Failed to create chat: ', err);
    }
  }

  async function openChat(id: string) {
    const found = chats.find(c => c.id === id);
    if (!found) return;
    setStatus('');
    try {
      setMessages(await api.messages(id));
      setChat(found);
    } catch (err) {
      fail('Could not open the chat: ', err);
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
      fail('Failed to ingest documents: ', err);
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
      fail('Failed to send message: ', err);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <div className="account">
        <span>Signed in as <strong>{user.name}</strong> &lt;{user.email}&gt;</span>
        <button type="button" onClick={onSignOut}>Sign out</button>
      </div>

      {status && <p className="status">{status}</p>}

      <section className="card">
        <div className="row">
          <label>Model
            <select value={model} onChange={e => setModel(e.target.value)}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <button type="button" onClick={newChat}>New chat</button>
        </div>
        {chats.length > 0 && (
          <label>Your chats
            <select value={chat?.id ?? ''} onChange={e => openChat(e.target.value)}>
              <option value="" disabled>Pick a chat…</option>
              {chats.map(c => (
                <option key={c.id} value={c.id}>
                  {c.title} · {c.model_id} · {new Date(c.created_at).toLocaleString()}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      {chat && (
        <>
          <details className="card">
            <summary>Add documents for context (optional, up to {MAX_DOCS})</summary>
            {docs.map((d, i) => (
              <label key={i}>Text {i + 1}
                <textarea value={d} onChange={e => setDocs(ds => ds.map((x, j) => (j === i ? e.target.value : x)))} />
              </label>
            ))}
            <button type="button" onClick={ingest}>Ingest</button>
          </details>

          <section className="card">
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
