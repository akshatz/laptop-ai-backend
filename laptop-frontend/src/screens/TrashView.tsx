import { useEffect, useState } from 'react';
import { api, documentUrl, type Chat, type Trash } from '../api';

interface Props {
  onError: (prefix: string, err: unknown) => void;
  // A chat came back: the screen reloads the chat list and opens it.
  onChatRestored: (chat: Chat) => void;
  // A document came back: the documents lists reload.
  onDocumentRestored: () => void;
}

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function daysLeft(deletedAt: string, purgeAfterDays: number) {
  const left = Math.ceil(purgeAfterDays - (Date.now() - new Date(deletedAt).getTime()) / 86_400_000);
  return left <= 1 ? 'removed for good within a day' : `removed for good in ${left} days`;
}

// Your deleted chats and documents (laptop-backend GET /trash), with Restore; they're removed
// for good after purge_after_days (TRASH_DAYS, 30 by default).
export default function TrashView({ onError, onChatRestored, onDocumentRestored }: Props) {
  const [trash, setTrash] = useState<Trash | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = () => api.trash().then(setTrash).catch(err => onError('Could not load deleted items: ', err));
  useEffect(() => { reload(); }, []);

  async function restoreChat(id: string) {
    setBusy(id);
    try {
      onChatRestored(await api.restoreChat(id));
    } catch (err) {
      onError('Could not restore the chat: ', err);
    } finally {
      setBusy(null);
    }
  }

  async function restoreDocument(id: string) {
    setBusy(id);
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

  if (!trash) return <section className="documents"><p className="muted">Loading…</p></section>;
  const empty = !trash.chats.length && !trash.documents.length;

  return (
    <section className="documents trash">
      <h2>Deleted</h2>
      <p className="muted">
        Deleted chats and documents stay here for {trash.purge_after_days} days, then they're removed for good.
        Restoring a chat brings back its messages and documents.
      </p>
      {empty && <p className="muted">Nothing deleted.</p>}

      {trash.chats.length > 0 && <h3>Chats</h3>}
      <ul className="trash-list">
        {trash.chats.map(c => (
          <li key={c.id}>
            <div className="trash-main">
              <strong title={c.title}>💬 {c.title}</strong>
              <small className="muted">
                {c.documents > 0 && `${c.documents} document${c.documents === 1 ? '' : 's'} · `}
                deleted {new Date(c.deleted_at).toLocaleString()} · {daysLeft(c.deleted_at, trash.purge_after_days)}
              </small>
            </div>
            <button type="button" onClick={() => restoreChat(c.id)} disabled={busy !== null}>
              {busy === c.id ? 'Restoring…' : '↩ Restore'}
            </button>
          </li>
        ))}
      </ul>

      {trash.documents.length > 0 && <h3>Documents</h3>}
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
            <button type="button" onClick={() => restoreDocument(d.id)} disabled={busy !== null}>
              {busy === d.id ? 'Restoring…' : '↩ Restore'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
