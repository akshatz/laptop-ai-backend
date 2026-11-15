import { useEffect, useState } from 'react';
import { api, documentUrl, type Trash } from '../api';

interface Props {
  onError: (prefix: string, err: unknown) => void;
  // A document came back: the documents lists reload.
  onDocumentRestored: () => void;
}

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function daysLeft(deletedAt: string, purgeAfterDays: number) {
  const left = Math.ceil(purgeAfterDays - (Date.now() - new Date(deletedAt).getTime()) / 86_400_000);
  return left <= 1 ? 'removed for good within a day' : `removed for good in ${left} days`;
}

// Your deleted documents (laptop-backend GET /trash; its deleted chats aren't listed here). Each row can be restored, or
// deleted for good right away (DELETE /trash/...: gone from the database and from Milvus, no way
// back); otherwise it's removed for good after purge_after_days (TRASH_DAYS, 30 by default).
export default function TrashView({ onError, onDocumentRestored }: Props) {
  const [trash, setTrash] = useState<Trash | null>(null);
  // The row being restored or deleted for good; the other buttons wait meanwhile.
  const [busy, setBusy] = useState<{ id: string; action: 'restore' | 'purge' } | null>(null);

  const reload = () => api.trash().then(setTrash).catch(err => onError('Could not load deleted items: ', err));
  useEffect(() => { reload(); }, []);

  async function restoreDocument(id: string) {
    setBusy({ id, action: 'restore' });
    try {
      await api.restoreDocument(id);
      onDocumentRestored();
      await reload();
    } catch (err) {
      onError('Could not restore the document: ', err);
    } finally {
      setBusy(null);
    }
  }

  async function purgeDocument(d: Trash['documents'][number]) {
    if (!confirm(`Delete “${d.filename}” for good? It is removed permanently, also from search. This can't be undone.`)) return;
    setBusy({ id: d.id, action: 'purge' });
    try {
      await api.purgeDocument(d.id);
      await reload();
    } catch (err) {
      onError('Could not delete the document for good: ', err);
    } finally {
      setBusy(null);
    }
  }

  // Restore and Delete for good, the same pair on every row.
  const actions = (id: string, restore: () => void, purge: () => void) => (
    <div className="trash-actions">
      <button type="button" onClick={restore} disabled={busy !== null}>
        {busy?.id === id && busy.action === 'restore' ? 'Restoring…' : '↩ Restore'}
      </button>
      <button type="button" className="danger" onClick={purge} disabled={busy !== null}
        title="Remove it now, permanently, instead of waiting">
        {busy?.id === id && busy.action === 'purge' ? 'Deleting…' : '🗑 Delete for good'}
      </button>
    </div>
  );

  if (!trash) return <section className="documents"><p className="muted">Loading…</p></section>;
  const empty = !trash.documents.length;

  return (
    <section className="documents trash">
      <h2>Deleted</h2>
      <p className="muted">
        Deleted documents stay here for {trash.purge_after_days} days, then they're removed for good.
        Delete for good removes one right away, and that can't be undone.
      </p>
      {empty && <p className="muted">Nothing deleted.</p>}

      <ul className="trash-list">
        {trash.documents.map(d => (
          <li key={d.id}>
            <div className="trash-main">
              <a href={documentUrl(d.id)} target="_blank" rel="noreferrer" title={d.filename}>📄 {d.filename}</a>
              <small className="muted">
                {d.version > 1 && `v${d.version} · `}{size(d.size_bytes)} · in “{d.chat_title}” · deleted{' '}
                {new Date(d.deleted_at).toLocaleString()} · {daysLeft(d.deleted_at, trash.purge_after_days)}
              </small>
            </div>
            {actions(d.id, () => restoreDocument(d.id), () => purgeDocument(d))}
          </li>
        ))}
      </ul>
    </section>
  );
}
