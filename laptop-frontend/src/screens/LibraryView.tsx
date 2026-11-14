import { useEffect, useMemo, useState } from 'react';
import { api, documentUrl, type ChatDocument, type User } from '../api';
import DocumentHistory from './DocumentHistory';

interface Props {
  user: User;
  onError: (prefix: string, err: unknown) => void;
  onOpenChat: (chatId: string) => void;
  // Changes when a background embedding finishes.
  reloadKey: number;
}

type StatusFilter = 'all' | ChatDocument['status'];

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function icon(d: ChatDocument) {
  const name = d.filename.toLowerCase();
  if (name.endsWith('.pdf')) return '📕';
  if (name.endsWith('.docx')) return '📘';
  if (/\.(csv|tsv)$/.test(name)) return '📊';
  if (/\.(json|ya?ml|xml|html?)$/.test(name)) return '🧾';
  if (name.startsWith('pasted text')) return '📋';
  return '📄';
}

const STATUS: Record<ChatDocument['status'], string> = {
  embedding: '⏳ Embedding', ready: '✓ Ready', failed: '✕ Failed', replaced: 'Replaced',
};

// Every document the viewer may see, as cards: their own, or everyone's for admins
// (laptop-backend's GET /documents?all=true, users.role == "admin").
export default function LibraryView({ user, onError, onOpenChat, reloadKey }: Props) {
  const isAdmin = user.role === 'admin';
  const [everyone, setEveryone] = useState(isAdmin);
  const [docs, setDocs] = useState<ChatDocument[] | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  // Key (owner + file name) of the file whose history panel is open.
  const [historyKey, setHistoryKey] = useState<string | null>(null);

  useEffect(() => {
    api.documents(isAdmin && everyone).then(setDocs).catch(err => onError('Could not load documents: ', err));
  }, [everyone, reloadKey]);

  async function remove(d: ChatDocument) {
    const whose = d.mine ? '' : ` (${d.owner_name}'s)`;
    if (!confirm(`Delete “${d.filename}”${whose}? Answers in “${d.chat_title}” stop using it. This can't be undone.`)) return;
    try {
      await api.deleteDocument(d.id);
      setDocs(ds => (ds ?? []).filter(x => x.id !== d.id));
    } catch (err) {
      onError('Could not delete the document: ', err);
    }
  }

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (docs ?? []).filter(d =>
      (statusFilter === 'all' || d.status === statusFilter) &&
      (!q || [d.filename, d.chat_title, d.owner_name ?? '', d.owner_email ?? ''].some(t => t.toLowerCase().includes(q))));
  }, [docs, search, statusFilter]);

  // One card per file name and owner (across chats), showing the newest upload; the others are
  // its versions, listed in the history panel. `docs` is newest first already.
  const groupKey = (d: ChatDocument) => `${d.owner_email ?? ''}\u0000${d.filename}`;
  const versionsOf = useMemo(() => {
    const map = new Map<string, ChatDocument[]>();
    for (const d of docs ?? []) map.set(groupKey(d), [...(map.get(groupKey(d)) ?? []), d]);
    return map;
  }, [docs]);
  const cards = useMemo(() => {
    const seen = new Set<string>();
    return shown.filter(d => !seen.has(groupKey(d)) && seen.add(groupKey(d)));
  }, [shown]);
  const history = historyKey ? versionsOf.get(historyKey) : undefined;

  return (
    <section className="documents library">
      <div className="dash-head">
        <h2>{isAdmin && everyone ? 'All documents' : 'My documents'}</h2>
        {isAdmin && (
          <div className="segmented" role="group" aria-label="Whose documents">
            <button type="button" className={everyone ? 'on' : ''} aria-pressed={everyone} onClick={() => setEveryone(true)}>Everyone</button>
            <button type="button" className={!everyone ? 'on' : ''} aria-pressed={!everyone} onClick={() => setEveryone(false)}>Only mine</button>
          </div>
        )}
      </div>

      <div className="library-filters">
        <input type="search" placeholder={isAdmin && everyone ? 'Search name, chat or person' : 'Search name or chat'}
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search documents" />
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)} aria-label="Status">
          <option value="all">Any status</option>
          <option value="ready">Ready</option>
          <option value="embedding">Embedding</option>
          <option value="failed">Failed</option>
          <option value="replaced">Replaced (older versions)</option>
        </select>
      </div>

      {docs === null ? <p className="muted">Loading…</p> : shown.length === 0 ? (
        <p className="muted">{docs.length === 0 ? 'No documents yet. Add some under 📄 Documents.' : 'No documents match.'}</p>
      ) : (
        <>
          <p className="muted library-count">{cards.length} file{cards.length === 1 ? '' : 's'} · {shown.length} of {docs.length} upload{docs.length === 1 ? '' : 's'}</p>
          <div className="doc-cards">
            {cards.map(d => (
              <article key={d.id} className={'doc-card ' + d.status + (historyKey === groupKey(d) ? ' selected' : '')}>
                <div className="doc-card-top">
                  {/\.(png|jpe?g|gif|webp)$/i.test(d.filename)
                    ? <a href={documentUrl(d.id)} target="_blank" rel="noreferrer" tabIndex={-1} aria-hidden="true">
                        <img className="doc-card-thumb" src={documentUrl(d.id)} alt="" loading="lazy" />
                      </a>
                    : <span className="doc-card-icon" aria-hidden="true">{icon(d)}</span>}
                  <span className={'chip ' + d.status} title={d.error ?? undefined}>{STATUS[d.status] ?? d.status}</span>
                  <button type="button" className="icon-button doc-card-delete" onClick={() => remove(d)}
                    disabled={d.status === 'embedding'} aria-label={`Delete ${d.filename}`}
                    title={d.status === 'embedding' ? 'Can be deleted once it is ready' : 'Delete'}>🗑</button>
                </div>
                <a className="doc-card-name" href={documentUrl(d.id)} target="_blank" rel="noreferrer" title={d.filename}>
                  {d.filename}
                </a>
                {d.version > 1 && <span className="chip version">v{d.version}</span>}
                {(versionsOf.get(groupKey(d))?.length ?? 0) > 1 && (
                  <button type="button" className="link doc-card-versions" onClick={() => setHistoryKey(groupKey(d))}>
                    🕘 {versionsOf.get(groupKey(d))!.length} versions
                  </button>
                )}
                {d.status === 'failed' && d.error && <p className="doc-card-error">{d.error}</p>}
                <dl className="doc-card-meta">
                  {isAdmin && everyone && <><dt>Owner</dt><dd title={d.owner_email ?? undefined}>{d.mine ? 'You' : d.owner_name}</dd></>}
                  <dt>Chat</dt>
                  <dd>{d.mine
                    ? <button type="button" className="link" onClick={() => onOpenChat(d.chat_id)}>{d.chat_title}</button>
                    : d.chat_title}</dd>
                  <dt>Size</dt><dd>{size(d.size_bytes)}{d.chunks ? ` · ${d.chunks} chunks` : ''}</dd>
                  <dt>Added</dt><dd>{new Date(d.created_at).toLocaleString()}</dd>
                </dl>
              </article>
            ))}
          </div>
        </>
      )}
      {history && (
        <DocumentHistory versions={history} onClose={() => setHistoryKey(null)} onOpenChat={onOpenChat} />
      )}
    </section>
  );
}
