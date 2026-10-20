import { useEffect, useMemo, useState } from 'react';
import { api, documentUrl, type ChatDocument, type User } from '../api';

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
// (custom-backend's GET /documents?all=true, users.role == "admin").
export default function LibraryView({ user, onError, onOpenChat, reloadKey }: Props) {
  const isAdmin = user.role === 'admin';
  const [everyone, setEveryone] = useState(isAdmin);
  const [docs, setDocs] = useState<ChatDocument[] | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  useEffect(() => {
    api.documents(isAdmin && everyone).then(setDocs).catch(err => onError('Could not load documents: ', err));
  }, [everyone, reloadKey]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (docs ?? []).filter(d =>
      (statusFilter === 'all' || d.status === statusFilter) &&
      (!q || [d.filename, d.chat_title, d.owner_name ?? '', d.owner_email ?? ''].some(t => t.toLowerCase().includes(q))));
  }, [docs, search, statusFilter]);

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
          <p className="muted library-count">{shown.length} of {docs.length} document{docs.length === 1 ? '' : 's'}</p>
          <div className="doc-cards">
            {shown.map(d => (
              <article key={d.id} className={'doc-card ' + d.status}>
                <div className="doc-card-top">
                  <span className="doc-card-icon" aria-hidden="true">{icon(d)}</span>
                  <span className={'chip ' + d.status} title={d.error ?? undefined}>{STATUS[d.status] ?? d.status}</span>
                </div>
                <a className="doc-card-name" href={documentUrl(d.id)} target="_blank" rel="noreferrer" title={d.filename}>
                  {d.filename}
                </a>
                {d.version > 1 && <span className="chip version">v{d.version}</span>}
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
    </section>
  );
}
