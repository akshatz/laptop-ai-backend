import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { api, HttpError, SIGN_IN_URL, type Chat, type Message, type User } from '../api';

interface Props {
  // null: signed out; the same layout asks the person to sign in.
  user: User | null;
  notice: string;
  onSignOut: () => void;
  onSessionExpired: () => void;
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';

const MAX_DOCS = 5;

type View = 'chat' | 'documents';
type Shown = Pick<Message, 'role' | 'content' | 'web_searched' | 'web_sources'>;

// Same rule as custom-backend's send_message: a new chat is named after its first question.
const titleFrom = (text: string) => (text.length > 60 ? text.slice(0, 57).trimEnd() + '…' : text);

export default function ChatScreen({ user, notice, onSignOut, onSessionExpired }: Props) {
  const [status, setStatus] = useState(notice);
  const [models, setModels] = useState<string[]>(['llama3.2']);
  const [model, setModel] = useState('llama3.2');
  const [chats, setChats] = useState<Chat[]>([]);
  const [chat, setChat] = useState<Chat | null>(null);
  const [docs, setDocs] = useState<string[]>(Array(MAX_DOCS).fill(''));
  const [messages, setMessages] = useState<Shown[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [view, setView] = useState<View>('chat');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  function fail(prefix: string, err: unknown) {
    if (err instanceof HttpError && err.status === 401) { onSessionExpired(); return; }
    setStatus(prefix + (err as Error).message);
  }

  useEffect(() => {
    if (!user) return;
    // Fill the model list from Ollama; on failure llama3.2 stays.
    api.models().then(({ default: def, models }) => {
      if (!models.length) return;
      const wanted = def.includes(':') ? def : def + ':latest';
      setModels(models);
      setModel(models.includes(wanted) ? wanted : models[0]);
    }).catch(err => fail('Could not load the model list: ', err));
    api.chats().then(setChats).catch(err => fail('Could not load your chats: ', err));
  }, [user?.id]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, sending]);

  // "New chat" only clears the screen; the chat is created with the first message (or the first
  // documents), so empty chats don't pile up in the history.
  function newChat() {
    setStatus('');
    setChat(null);
    setMessages([]);
    setView('chat');
    setSidebarOpen(false);
  }

  async function openChat(c: Chat) {
    setStatus('');
    setView('chat');
    setSidebarOpen(false);
    try {
      setMessages(await api.messages(c.id));
      setChat(c);
    } catch (err) {
      fail('Could not open the chat: ', err);
    }
  }

  async function ensureChat(): Promise<Chat | null> {
    if (chat) return chat;
    try {
      const created = await api.createChat(model);
      setChats(cs => [created, ...cs]);
      setChat(created);
      return created;
    } catch (err) {
      fail('Failed to create chat: ', err);
      return null;
    }
  }

  async function ingest() {
    const texts = docs.map(d => d.trim()).filter(Boolean);
    if (!texts.length) { setStatus('Paste some text first.'); return; }
    setStatus('');
    const target = await ensureChat();
    if (!target) return;
    try {
      const r = await api.ingest(target.id, texts);
      setDocs(Array(MAX_DOCS).fill(''));
      setStatus(`Added ${r.documents_ingested} document(s) (${r.chunks_ingested} chunks) to this chat.`);
    } catch (err) {
      fail('Failed to add documents: ', err);
    }
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setStatus('');
    setSending(true);
    const target = await ensureChat();
    if (!target) { setSending(false); return; }
    const first = messages.length === 0;
    setInput('');
    setMessages(m => [...m, { role: 'user', content }]);
    if (first) {
      const renamed = { ...target, title: titleFrom(content) };
      setChat(renamed);
      setChats(cs => cs.map(c => (c.id === target.id ? renamed : c)));
    }
    try {
      const reply = await api.send(target.id, content);
      setMessages(m => [...m, reply]);
    } catch (err) {
      fail('Failed to send message: ', err);
    } finally {
      setSending(false);
    }
  }

  // Enter sends, Shift+Enter adds a line.
  function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <div className={'layout' + (sidebarOpen ? ' sidebar-open' : '')}>
      <aside className="sidebar">
        <div className="brand">Laptop Dev Chat</div>
        <button type="button" className="side-action" onClick={newChat} disabled={!user}>＋ New chat</button>
        <button type="button" className={'side-action' + (view === 'documents' ? ' active' : '')} disabled={!user}
          onClick={() => { setView('documents'); setSidebarOpen(false); }}>
          📄 Add documents
        </button>

        <div className="side-heading">Recent chats</div>
        <nav className="history">
          {!user && <p className="muted">Sign in to see your chats.</p>}
          {user && chats.length === 0 && <p className="muted">No chats yet.</p>}
          {user && chats.map(c => (
            <button type="button" key={c.id}
              className={'history-item' + (chat?.id === c.id && view === 'chat' ? ' active' : '')}
              onClick={() => openChat(c)} title={c.title}>
              <span>{c.title}</span>
              <small>{new Date(c.created_at).toLocaleDateString()} · {c.model_id.replace(/:latest$/, '')}</small>
            </button>
          ))}
        </nav>

        <div className="account">
          {user ? (
            <>
              <span className="avatar" aria-hidden="true">{initials(user.name)}</span>
              <div className="who"><strong>{user.name}</strong><small>{user.email}</small></div>
              <button type="button" className="icon-button" onClick={onSignOut} title="Sign out" aria-label="Sign out">⎋</button>
            </>
          ) : (
            <a className="signin-link" href={SIGN_IN_URL}><span className="avatar" aria-hidden="true">👤</span> Sign in</a>
          )}
        </div>
      </aside>

      <div className="backdrop" onClick={() => setSidebarOpen(false)} />

      <main className="content">
        <header className="topbar">
          <button type="button" className="menu-toggle" aria-label="Menu" onClick={() => setSidebarOpen(o => !o)}>☰</button>
          <span className="title">{view === 'documents' ? 'Add documents' : chat ? chat.title : 'New chat'}</span>
          <label className="model"><span>Model</span>
            <select value={chat ? chat.model_id : model} disabled={!!chat || !user} onChange={e => setModel(e.target.value)}
              title={chat ? 'A chat keeps the model it was started with' : undefined}>
              {(chat && !models.includes(chat.model_id) ? [chat.model_id, ...models] : models)
                .map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        </header>

        {status && <p className="status">{status}</p>}

        {!user ? (
          <section className="messages">
            <div className="thread empty">
              <h2>Welcome to Laptop Dev Chat</h2>
              <p className="muted">Sign in with your family account, the same one you use for Open WebUI.</p>
              <a className="button" href={SIGN_IN_URL}>Sign in</a>
            </div>
          </section>
        ) : view === 'documents' ? (
          <section className="documents">
            <p>
              Texts added here are used as context for answers in{' '}
              {chat ? <strong>{chat.title}</strong> : 'a new chat (started when you add them)'}, up to {MAX_DOCS} at a time.
              Paste text; file upload isn't supported yet.
            </p>
            {docs.map((d, i) => (
              <label key={i}>Text {i + 1}
                <textarea value={d} onChange={e => setDocs(ds => ds.map((x, j) => (j === i ? e.target.value : x)))} />
              </label>
            ))}
            <button type="button" onClick={ingest}>Add to chat</button>
            {chat && <button type="button" onClick={() => setView('chat')}>Back to chat</button>}
          </section>
        ) : (
          <>
            <section className="messages">
              <div className="thread">
                {messages.length === 0 && !sending && (
                  <div className="empty">
                    <h2>How can I help, {user.name.split(' ')[0]}?</h2>
                    <p className="muted">Questions about current events are searched on the web automatically.</p>
                  </div>
                )}
                {messages.map((m, i) => (
                  <div key={i} className={'msg ' + m.role}>
                    <div className="bubble">{m.content}</div>
                    {m.web_sources && m.web_sources.length > 0 && (
                      <div className="sources">
                        🔎 Web sources:{' '}
                        {m.web_sources.map((s, j) => (
                          <a key={s.url} href={s.url} target="_blank" rel="noreferrer" title={s.url}>[{j + 1}] {s.title || s.url}</a>
                        ))}
                      </div>
                    )}
                    {m.web_searched && !m.web_sources?.length && <div className="sources">🔎 Searched the web, nothing found.</div>}
                  </div>
                ))}
                {sending && <div className="msg assistant"><div className="bubble typing">Thinking…</div></div>}
                <div ref={endRef} />
              </div>
            </section>
            <form onSubmit={send} className="composer">
              <div className="composer-box">
                <textarea rows={1} placeholder="Send a message (Shift+Enter for a new line)" value={input}
                  onChange={e => setInput(e.target.value)} onKeyDown={onComposerKey} autoFocus />
                <button type="submit" disabled={sending || !input.trim()} aria-label="Send">➤</button>
              </div>
            </form>
          </>
        )}
      </main>
    </div>
  );
}
