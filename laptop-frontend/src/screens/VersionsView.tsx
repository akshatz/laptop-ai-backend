import { useEffect, useMemo, useState } from 'react';
import { api, documentUrl, type ChatDocument } from '../api';
import VersionTimeline from './VersionTimeline';

interface Props {
  onError: (prefix: string, err: unknown) => void;
  onOpenChat: (chatId: string) => void;
  // Changes when a background embedding finishes or a document is deleted or restored.
  reloadKey: number;
}

// The 🕘 Versions page: every file that was uploaded more than once, each with its timeline
// (same rule as the panel in All documents: one file name per owner, across chats). Documents are
// visible to everyone, so it lists everyone's, or only yours.
export default function VersionsView({ onError, onOpenChat, reloadKey }: Props) {
  const [everyone, setEveryone] = useState(true);
  const [docs, setDocs] = useState<ChatDocument[] | null>(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.documents(everyone).then(setDocs).catch(err => onError('Could not load documents: ', err));
  }, [everyone, reloadKey]);

  // `docs` is newest first already, so each group is too.
  const { groups, single } = useMemo(() => {
    const map = new Map<string, ChatDocument[]>();
    for (const d of docs ?? []) {
      const key = `${d.owner_id ?? d.owner_email ?? ''}\u0000${d.filename}`;
      map.set(key, [...(map.get(key) ?? []), d]);
    }
    const q = search.trim().toLowerCase();
    const all = [...map.entries()].map(([key, versions]) => ({ key, versions }));
    const shown = all.filter(g => g.versions.length > 1 &&
      (!q || [g.versions[0].filename, g.versions[0].owner_name ?? ''].some(t => t.toLowerCase().includes(q))));
    return { groups: shown, single: all.filter(g => g.versions.length === 1).length };
  }, [docs, search]);

  return (
    <section className="documents library">
      <div className="dash-head">
        <h2>Versions</h2>
        <div className="segmented" role="group" aria-label="Whose documents">
          <button type="button" className={everyone ? 'on' : ''} aria-pressed={everyone} onClick={() => setEveryone(true)}>Everyone</button>
          <button type="button" className={!everyone ? 'on' : ''} aria-pressed={!everyone} onClick={() => setEveryone(false)}>Only mine</button>
        </div>
      </div>
      <p className="muted library-note">
        Files uploaded more than once, with every upload newest first: <em>changed</em> when the content differs
        from the upload before it, <em>same content</em> when it is identical.
      </p>
      <div className="library-filters">
        <input type="search" placeholder="Search name or person" value={search}
          onChange={e => setSearch(e.target.value)} aria-label="Search files" />
      </div>

      {docs === null ? <p className="muted">Loading…</p> : groups.length === 0 ? (
        <p className="muted">{search.trim() ? 'No files match.' : 'No file has more than one upload yet.'}</p>
      ) : (
        <>
          <p className="muted library-count">
            {groups.length} file{groups.length === 1 ? '' : 's'} with versions
            {single > 0 && ` · ${single} with a single upload not listed`}
          </p>
          {groups.map(g => (
            <article key={g.key} className="version-group">
              <h3>
                <a href={documentUrl(g.versions[0].id)} target="_blank" rel="noreferrer" title={g.versions[0].filename}>
                  {g.versions[0].filename}
                </a>
              </h3>
              <p className="muted version-group-meta">
                {everyone && <>{g.versions[0].mine ? 'You' : g.versions[0].owner_name} · </>}
                {g.versions.length} uploads
              </p>
              <VersionTimeline versions={g.versions} onOpenChat={onOpenChat} />
            </article>
          ))}
        </>
      )}
    </section>
  );
}
