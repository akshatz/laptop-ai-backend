import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import DocumentsView from './DocumentsView';
import FeedbackView from './FeedbackView';
import UploadSnackbar, { type UploadItem } from './UploadSnackbar';
import { api, HttpError, SIGN_IN_URL, type Chat, type Feedback, type Message, type User } from '../api';

interface Props {
  // null: signed out; the same layout asks the person to sign in.
  user: User | null;
  notice: string;
  onSignOut: () => void;
  onSessionExpired: () => void;
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';

type View = 'chat' | 'documents' | 'feedback';
type Shown = Pick<Message, 'role' | 'content' | 'web_searched' | 'web_sources'> & Partial<Pick<Message, 'id' | 'feedback'>>;

// Same rule as custom-backend's send_message: a new chat is named after its first question.
const titleFrom = (text: string) => (text.length > 60 ? text.slice(0, 57).trimEnd() + '…' : text);

export default function ChatScreen({ user, notice, onSignOut, onSessionExpired }: Props) {
  const [status, setStatus] = useState(notice);
  const [models, setModels] = useState<string[]>(['llama3.2']);
  const [model, setModel] = useState('llama3.2');
  const [chats, setChats] = useState<Chat[]>([]);
  const [chat, setChat] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<Shown[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [view, setView] = useState<View>('chat');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // From custom-backend's MAX_QUESTIONS_PER_CHAT (0 = no limit); it refuses more with a 409.
  const [maxQuestions, setMaxQuestions] = useState(1);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  // Bumped when an embedding finishes, so the Documents list reloads.
  const [docsVersion, setDocsVersion] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  function fail(prefix: string, err: unknown) {
    if (err instanceof HttpError && err.status === 401) { onSessionExpired(); return; }
    setStatus(prefix + (err as Error).message);
  }

  useEffect(() => {
    if (!user) return;
    // Fill the model list from Ollama; on failure llama3.2 stays.
    api.models().then(({ default: def, models, max_questions_per_chat }) => {
      if (max_questions_per_chat !== undefined) setMaxQuestions(max_questions_per_chat);
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

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const content = input.trim();
    if (!content || sending || chatFull) return;
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

  // Clicking the selected thumb again clears the rating.
  async function rate(m: Shown, rating: 1 | -1) {
    if (!chat || !m.id) return;
    const next: Feedback = m.feedback === rating ? null : rating;
    setMessages(ms => ms.map(x => (x.id === m.id ? { ...x, feedback: next } : x)));
    try {
      await api.rate(chat.id, m.id, next);
    } catch (err) {
      setMessages(ms => ms.map(x => (x.id === m.id ? { ...x, feedback: m.feedback } : x)));
      fail('Failed to save feedback: ', err);
    }
  }

  const asked = messages.filter(m => m.role === 'user').length;
  const chatFull = maxQuestions > 0 && asked >= maxQuestions && !sending;

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
          📄 Documents
        </button>
        <button type="button" className={'side-action' + (view === 'feedback' ? ' active' : '')} disabled={!user}
          onClick={() => { setView('feedback'); setSidebarOpen(false); }}>
          📊 Feedback
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
          <span className="title">{view === 'documents' ? 'Documents' : view === 'feedback' ? 'Feedback' : chat ? chat.title : 'New chat'}</span>
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
          <DocumentsView chat={chat} ensureChat={ensureChat} onError={fail}
            setUploads={setUploads} reloadKey={docsVersion}
            onOpenChat={id => { const c = chats.find(x => x.id === id); if (c) openChat(c); }}
            onBack={() => setView('chat')} />
        ) : view === 'feedback' ? (
          <FeedbackView onError={fail}
            onOpenChat={id => { const c = chats.find(x => x.id === id); if (c) openChat(c); }} />
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
                    {m.role === 'assistant' && m.id && (
                      <div className="feedback">
                        <button type="button" title="Good answer" aria-label="Good answer" aria-pressed={m.feedback === 1}
                          className={m.feedback === 1 ? 'selected' : ''} onClick={() => rate(m, 1)}>👍</button>
                        <button type="button" title="Bad answer" aria-label="Bad answer" aria-pressed={m.feedback === -1}
                          className={m.feedback === -1 ? 'selected' : ''} onClick={() => rate(m, -1)}>👎</button>
                      </div>
                    )}
                  </div>
                ))}
                {sending && <div className="msg assistant"><div className="bubble typing">Thinking…</div></div>}
                <div ref={endRef} />
              </div>
            </section>
            {chatFull ? (
              <div className="composer chat-full">
                <p className="muted">
                  {maxQuestions === 1 ? 'Each chat answers one question.' : `Each chat answers up to ${maxQuestions} questions.`}{' '}
                  Ask your next one in a new chat.
                </p>
                <button type="button" className="primary" onClick={newChat}>＋ New chat</button>
              </div>
            ) : (
            <form onSubmit={send} className="composer">
              <div className="composer-box">
                <textarea rows={1} value={input} disabled={sending} aria-busy={sending}
                  placeholder={sending ? 'Waiting for the answer…' : 'Send a message (Shift+Enter for a new line)'}
                  onChange={e => setInput(e.target.value)} onKeyDown={onComposerKey} autoFocus />
                <button type="submit" disabled={sending || !input.trim()} aria-label="Send">➤</button>
              </div>
            </form>
            )}
          </>
        )}
      </main>
      <UploadSnackbar items={uploads} setItems={setUploads} onDone={() => setDocsVersion(v => v + 1)} />
    </div>
  );
}
