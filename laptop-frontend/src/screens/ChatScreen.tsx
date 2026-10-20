import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { copyText } from '../copy';
import DocumentsView from './DocumentsView';
import FeedbackView from './FeedbackView';
import LibraryView from './LibraryView';
import AdminSessionsView from './AdminSessionsView';
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

type View = 'chat' | 'documents' | 'library' | 'feedback' | 'sessions';
type Shown = Pick<Message, 'role' | 'content' | 'web_searched' | 'web_sources'> & Partial<Pick<Message, 'id' | 'feedback' | 'regenerations' | 'created_at'>>;

// The open screen and chat live in the URL hash (#/chat/<id>, #/documents, #/library,
// #/feedback, #/sessions; empty = Home), so a reload, Back/Forward or a copied link keeps them.
const VIEWS: View[] = ['documents', 'library', 'feedback', 'sessions'];
function readRoute(): { view: View; chatId: string | null } {
  const [, first, second] = location.hash.replace(/^#/, '').split('/');
  if (first === 'chat' && second) return { view: 'chat', chatId: decodeURIComponent(second) };
  if ((VIEWS as string[]).includes(first)) return { view: first as View, chatId: null };
  return { view: 'chat', chatId: null };
}
const routeHash = (view: View, chatId: string | null) =>
  view === 'chat' ? (chatId ? `#/chat/${encodeURIComponent(chatId)}` : '') : `#/${view}`;

// An answer still being written after a reload: the backend keeps going when the page goes
// away, so a question this recent without an answer is polled until the answer is saved.
const PENDING_WINDOW_MS = 5 * 60_000;
const POLL_PENDING_MS = 3000;
function awaitingAnswer(list: Shown[]) {
  const last = list[list.length - 1];
  return !!last && last.role === 'user' && !!last.created_at &&
    Date.now() - new Date(last.created_at).getTime() < PENDING_WINDOW_MS;
}

// Ollama names without a tag mean :latest (laptop-backend's chat_service._with_tag).
const withTag = (name: string) => (name.includes(':') ? name : name + ':latest');

// Start-page suggestions: one click asks them in a new chat.
const SUGGESTIONS = [
  { icon: '📰', text: "What are today's top news headlines in India?" },
  { icon: '📈', text: 'How did the Sensex and Nifty close today?' },
  { icon: '🌦️', text: "What's the weather forecast for Ahmedabad this week?" },
  { icon: '📄', text: 'Summarise the documents I added to this chat.' },
  { icon: '🍲', text: 'Suggest a quick vegetarian dinner recipe for four people.' },
  { icon: '✉️', text: 'Help me write a polite leave application to my manager.' },
];

// Same rule as laptop-backend's send_message: a new chat is named after its first question.
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
  const [view, setView] = useState<View>(() => readRoute().view);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // From laptop-backend's MAX_QUESTIONS_PER_CHAT (0 = no limit); it refuses more with a 409.
  const [maxQuestions, setMaxQuestions] = useState(0);
  // From laptop-backend's MAX_REGENERATIONS (0 = no limit).
  const [maxRegenerations, setMaxRegenerations] = useState(2);
  // The answer being regenerated (shown dimmed until the new one replaces it).
  const [regenerating, setRegenerating] = useState<string | null>(null);
  // The open chat's last question is still being answered by an earlier page (see awaitingAnswer).
  const [waiting, setWaiting] = useState(false);
  const busy = sending || waiting;
  // Index of the message just copied, for a short "Copied" confirmation.
  const [copied, setCopied] = useState<number | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  // Bumped when an embedding finishes, so the Documents list reloads.
  const [docsVersion, setDocsVersion] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLElement>(null);
  // The question to keep at the top of the view (just asked, or the latest one of a chat just
  // opened); its answer then renders below it instead of the view following the end.
  const [anchor, setAnchor] = useState<number | null>(null);
  // Blank space under the thread so a short answer can still sit below a question at the top.
  const [spacer, setSpacer] = useState(0);
  const [showDown, setShowDown] = useState(false);

  function fail(prefix: string, err: unknown) {
    if (err instanceof HttpError && err.status === 401) { onSessionExpired(); return; }
    setStatus(prefix + (err as Error).message);
  }

  useEffect(() => {
    if (!user) return;
    // Fill the model list from Ollama; on failure llama3.2 stays.
    api.models().then(({ default: def, models, max_questions_per_chat, max_regenerations }) => {
      if (max_questions_per_chat !== undefined) setMaxQuestions(max_questions_per_chat);
      if (max_regenerations !== undefined) setMaxRegenerations(max_regenerations);
      if (!models.length) return;
      const wanted = def.includes(':') ? def : def + ':latest';
      setModels(models);
      setModel(models.includes(wanted) ? wanted : models[0]);
    }).catch(err => fail('Could not load the model list: ', err));
    api.chats().then(list => {
      setChats(list);
      // Reopen the chat named in the URL (after a reload or from a link).
      const { chatId } = readRoute();
      const wanted = chatId && list.find(c => c.id === chatId);
      if (wanted) openChat(wanted);
      else if (chatId) history.replaceState(null, '', location.pathname + location.search);
    }).catch(err => fail('Could not load your chats: ', err));
  }, [user?.id]);

  // State → URL. A new screen or chat adds a history entry, so Back returns to the previous one.
  useEffect(() => {
    const want = routeHash(view, chat?.id ?? null);
    const have = location.hash === '#' ? '' : location.hash;
    // While the chat named in the URL is still loading, leave the URL alone.
    const pending = readRoute();
    if (view === 'chat' && !chat && pending.chatId && !chats.length) return;
    if (want !== have) history.pushState(null, '', want || location.pathname + location.search);
  }, [view, chat?.id]);

  // URL → state, for Back/Forward.
  useEffect(() => {
    const onPop = () => {
      const { view: v, chatId } = readRoute();
      if (v === 'chat' && chatId) {
        const c = chats.find(x => x.id === chatId);
        if (c && c.id !== chat?.id) openChat(c);
        return;
      }
      setView(v);
      if (v === 'chat') { setChat(null); setMessages([]); }
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [chats, chat?.id]);

  useEffect(() => {
    if (!waiting || !chat) return;
    const chatId = chat.id;
    const timer = setInterval(async () => {
      try {
        const list = await api.messages(chatId);
        if (!awaitingAnswer(list)) {
          setMessages(list);
          setWaiting(false);
        }
      } catch { /* try again next time */ }
    }, POLL_PENDING_MS);
    return () => clearInterval(timer);
  }, [waiting, chat?.id]);

  // No auto-scroll to the end: put the anchored question at the top once, then leave the view
  // alone while the answer renders (the ↓ button jumps to the end).
  useEffect(() => {
    const box = scrollRef.current;
    if (anchor === null || !box) return;
    const el = box.querySelector<HTMLElement>(`[data-index="${anchor}"]`);
    if (!el) return;
    setSpacer(Math.max(0, box.clientHeight - 160));
    requestAnimationFrame(() => box.scrollTo({ top: el.offsetTop - 16, behavior: 'smooth' }));
  }, [anchor]);

  function updateDown() {
    const box = scrollRef.current, end = endRef.current;
    if (!box || !end) return;
    setShowDown(end.offsetTop - (box.scrollTop + box.clientHeight) > 40);
  }
  useEffect(updateDown, [messages, busy, spacer]);

  function scrollToEnd() {
    const box = scrollRef.current, end = endRef.current;
    if (box && end) box.scrollTo({ top: end.offsetTop - box.clientHeight + 24, behavior: 'smooth' });
  }

  // "New chat" only clears the screen; the chat is created with the first message (or the first
  // documents), so empty chats don't pile up in the history.
  function newChat() {
    setWaiting(false);
    setAnchor(null);
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
      const list = await api.messages(c.id);
      setMessages(list);
      const lastQuestion = list.map(m => m.role).lastIndexOf('user');
      setAnchor(null);
      requestAnimationFrame(() => setAnchor(lastQuestion >= 0 ? lastQuestion : null));
      setWaiting(awaitingAnswer(list));
      setChat(c);
      // The picker shows the chat's model; a change applies from the next question.
      setModel(withTag(c.model_id));
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
    await ask(input.trim());
  }

  async function ask(content: string) {
    if (!content || busy || chatFull) return;
    setStatus('');
    setSending(true);
    const target = await ensureChat();
    if (!target) { setSending(false); return; }
    const first = messages.length === 0;
    setInput('');
    setAnchor(messages.length);
    setMessages(m => [...m, { role: 'user', content }]);
    if (first) {
      const renamed = { ...target, title: titleFrom(content) };
      setChat(renamed);
      setChats(cs => cs.map(c => (c.id === target.id ? renamed : c)));
    }
    try {
      const switched = withTag(target.model_id) !== model ? model : undefined;
      const reply = await api.send(target.id, content, switched);
      if (switched) {
        const updated = { ...target, model_id: switched };
        setChat(c => (c && c.id === target.id ? { ...c, model_id: switched } : c));
        setChats(cs => cs.map(c => (c.id === target.id ? { ...c, ...updated, title: c.title } : c)));
      }
      setMessages(m => [...m, reply]);
    } catch (err) {
      fail('Failed to send message: ', err);
    } finally {
      setSending(false);
    }
  }

  // Questions copy as typed; answers with their web sources as a numbered list.
  async function copyMessage(m: Shown, index: number) {
    const sources = m.role === 'assistant' && m.web_sources?.length
      ? '\n\nSources:\n' + m.web_sources.map((s, j) => `[${j + 1}] ${s.title ? s.title + ' – ' : ''}${s.url}`).join('\n')
      : '';
    if (await copyText(m.content + sources)) {
      setCopied(index);
      setTimeout(() => setCopied(c => (c === index ? null : c)), 1500);
    } else {
      setStatus('Could not copy to the clipboard.');
    }
  }

  // A new answer to the same question, replacing the latest one in place.
  async function regenerate(m: Shown) {
    if (!chat || !m.id || busy) return;
    setStatus('');
    setSending(true);
    setRegenerating(m.id);
    try {
      const reply = await api.regenerate(chat.id, m.id);
      setMessages(ms => ms.map(x => (x.id === m.id ? reply : x)));
    } catch (err) {
      fail('Could not regenerate the answer: ', err);
    } finally {
      setRegenerating(null);
      setSending(false);
    }
  }

  // The first rating is final (the backend refuses changes); Regenerate gives a new, unrated answer.
  async function rate(m: Shown, rating: 1 | -1) {
    if (!chat || !m.id || m.feedback != null) return;
    const next: Feedback = rating;
    setMessages(ms => ms.map(x => (x.id === m.id ? { ...x, feedback: next } : x)));
    try {
      await api.rate(chat.id, m.id, next);
    } catch (err) {
      setMessages(ms => ms.map(x => (x.id === m.id ? { ...x, feedback: m.feedback } : x)));
      fail('Failed to save feedback: ', err);
    }
  }

  const asked = messages.filter(m => m.role === 'user').length;
  const chatFull = maxQuestions > 0 && asked >= maxQuestions && !busy;

  // Enter sends, Shift+Enter adds a line.
  function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <div className={'layout' + (sidebarOpen ? ' sidebar-open' : '')}>
      <aside className="sidebar">
        <div className="brand">Laptop Dev Chat</div>
        <a href="/" className={'side-action' + (view === 'chat' && !chat ? ' active' : '')}
          onClick={e => { if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); newChat(); } }}>
          🏠 Home
        </a>
        <button type="button" className="side-action" onClick={newChat} disabled={!user}>＋ New chat</button>
        <button type="button" className={'side-action' + (view === 'documents' ? ' active' : '')} disabled={!user}
          onClick={() => { setView('documents'); setSidebarOpen(false); }}>
          📄 Documents
        </button>
        <button type="button" className={'side-action' + (view === 'library' ? ' active' : '')} disabled={!user}
          onClick={() => { setView('library'); setSidebarOpen(false); }}>
          🗂️ {user?.role === 'admin' ? 'All documents' : 'My documents'}
        </button>
        <button type="button" className={'side-action' + (view === 'feedback' ? ' active' : '')} disabled={!user}
          onClick={() => { setView('feedback'); setSidebarOpen(false); }}>
          📊 Feedback
        </button>
        {user?.role === 'admin' && (
          <button type="button" className={'side-action' + (view === 'sessions' ? ' active' : '')}
            onClick={() => { setView('sessions'); setSidebarOpen(false); }}>
            🛡️ Signed-in users
          </button>
        )}

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
          <span className="title">{view === 'documents' ? 'Documents' : view === 'library' ? (user?.role === 'admin' ? 'All documents' : 'My documents') : view === 'sessions' ? 'Signed-in users' : view === 'feedback' ? 'Feedback' : chat ? chat.title : 'New chat'}</span>
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
        ) : view === 'library' ? (
          <LibraryView user={user} onError={fail} reloadKey={docsVersion}
            onOpenChat={id => { const c = chats.find(x => x.id === id); if (c) openChat(c); }} />
        ) : view === 'sessions' && user.role === 'admin' ? (
          <AdminSessionsView onError={fail} />
        ) : view === 'feedback' ? (
          <FeedbackView onError={fail}
            onOpenChat={id => { const c = chats.find(x => x.id === id); if (c) openChat(c); }} />
        ) : (
          <>
            <section className="messages" ref={scrollRef} onScroll={updateDown}>
              <div className="thread">
                {messages.length === 0 && !busy && (
                  <div className="empty home">
                    <h2>How can I help, {user.name.split(' ')[0]}?</h2>
                    <p className="muted">Answers come only from your documents and a web search, with sources.</p>
                    <div className="suggestions">
                      {SUGGESTIONS.map(s => (
                        <button type="button" key={s.text} className="suggestion" onClick={() => ask(s.text)}>
                          <span aria-hidden="true">{s.icon}</span> {s.text}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {messages.map((m, i) => (
                  <div key={i} data-index={i} className={'msg ' + m.role + (m.id && m.id === regenerating ? ' regenerating' : '')}>
                    <div className="bubble">{m.content}</div>
                    {m.role === 'user' && (
                      <div className="msg-actions">
                        <button type="button" className="copy" onClick={() => copyMessage(m, i)}
                          title="Copy question" aria-label="Copy question">{copied === i ? '✓ Copied' : '⧉ Copy'}</button>
                      </div>
                    )}
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
                        <button type="button" className="copy" onClick={() => copyMessage(m, i)}
                          title="Copy answer (with sources)" aria-label="Copy answer">{copied === i ? '✓' : '⧉'}</button>
                        {m.feedback == null ? (
                          <>
                            <button type="button" title="Good answer" aria-label="Good answer" onClick={() => rate(m, 1)}>👍</button>
                            <button type="button" title="Bad answer" aria-label="Bad answer" onClick={() => rate(m, -1)}>👎</button>
                          </>
                        ) : (
                          <span className="rated" title="Thanks for your feedback"
                            aria-label={m.feedback === 1 ? 'Rated good' : 'Rated bad'}>{m.feedback === 1 ? '👍' : '👎'}</span>
                        )}
                        {i === messages.length - 1 && (() => {
                          const used = m.regenerations ?? 0;
                          const left = maxRegenerations > 0 ? maxRegenerations - used : Infinity;
                          return (
                            <button type="button" className="regen" onClick={() => regenerate(m)} disabled={busy || left <= 0}
                              title={left <= 0 ? 'No more regenerations for this answer' : left === Infinity ? 'Regenerate' : `Regenerate (${left} left)`}
                              aria-label="Regenerate answer">
                              ↻{maxRegenerations > 0 && <small> {used + 1}/{maxRegenerations + 1}</small>}
                            </button>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                ))}
                {busy && !regenerating && <div className="msg assistant"><div className="bubble typing">{waiting ? 'Still answering…' : 'Thinking…'}</div></div>}
                <div ref={endRef} />
                {anchor !== null && <div aria-hidden="true" style={{ height: spacer }} />}
              </div>
            </section>
            {showDown && (
              <button type="button" className="scroll-down" onClick={scrollToEnd} aria-label="Scroll to the end">↓</button>
            )}
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
                <textarea rows={1} value={input} disabled={busy} aria-busy={busy}
                  placeholder={busy ? 'Waiting for the answer…' : 'Send a message (Shift+Enter for a new line)'}
                  onChange={e => setInput(e.target.value)} onKeyDown={onComposerKey} autoFocus />
                <select className="composer-model" value={model} onChange={e => setModel(e.target.value)} disabled={busy || !user}
                  aria-label="Model" title={chat ? 'Model for the next question in this chat' : 'Model for this chat'}>
                  {(models.includes(model) ? models : [model, ...models]).map(m => (
                    <option key={m} value={m}>{m.replace(/:latest$/, '')}</option>
                  ))}
                </select>
                <button type="submit" disabled={busy || !input.trim()} aria-label="Send">➤</button>
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
