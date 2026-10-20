import { useEffect, useState } from 'react';
import { api, type UploadedDocument } from '../api';

// One line per file in the snackbar.
export interface UploadItem {
  key: string;
  filename: string;
  version?: number;
  // uploading → embedding → ready | failed; duplicate: already in the chat, nothing added.
  status: 'uploading' | 'embedding' | 'ready' | 'failed' | 'duplicate';
  id?: string;
  chunks?: number;
  error?: string | null;
}

interface Props {
  items: UploadItem[];
  setItems: (update: (items: UploadItem[]) => UploadItem[]) => void;
  onDone: () => void;
}

const POLL_MS = 1500;
const HIDE_AFTER_MS = 8000;

export const fromUploaded = (d: UploadedDocument): UploadItem => ({
  key: d.id + ':' + d.status, id: d.id, filename: d.filename, version: d.version,
  status: d.status === 'duplicate' ? 'duplicate' : 'embedding',
});

function line(i: UploadItem) {
  const v = i.version && i.version > 1 ? ` (version ${i.version})` : '';
  switch (i.status) {
    case 'uploading': return { icon: '⬆', text: `Uploading ${i.filename}…` };
    case 'embedding': return { icon: '⏳', text: `Embedding ${i.filename}${v}…` };
    case 'ready': return { icon: '✓', text: `${i.filename}${v} ready${i.chunks ? ` (${i.chunks} chunks)` : ''}${v ? ', replaces the earlier version' : ''}` };
    case 'failed': return { icon: '✕', text: `${i.filename}: embedding failed${i.error ? ` (${i.error})` : ''}` };
    case 'duplicate': return { icon: '↺', text: `${i.filename} is already in this chat, not added again` };
  }
}

// Upload progress: the backend stores files at once and embeds them into the vector store in the
// background; this polls /documents until every file is ready or failed, then hides itself.
export default function UploadSnackbar({ items, setItems, onDone }: Props) {
  const [hidden, setHidden] = useState(false);
  const pending = items.some(i => i.status === 'uploading' || i.status === 'embedding');
  const waiting = items.some(i => i.status === 'embedding');

  useEffect(() => { if (items.length) setHidden(false); }, [items.length]);

  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(async () => {
      try {
        const docs = await api.documents();
        const byId = new Map(docs.map(d => [d.id, d]));
        let changed = false;
        setItems(list => list.map(i => {
          const d = i.id && i.status === 'embedding' ? byId.get(i.id) : undefined;
          if (!d || d.status === 'embedding') return i;
          changed = true;
          return { ...i, status: d.status === 'failed' ? 'failed' : 'ready', chunks: d.chunks, error: d.error };
        }));
        if (changed) onDone();
      } catch { /* keep polling; a lost connection shouldn't end the snackbar */ }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting]);

  useEffect(() => {
    if (pending || !items.length || items.some(i => i.status === 'failed')) return;
    const timer = setTimeout(() => setItems(() => []), HIDE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [pending, items.length]);

  if (!items.length || hidden) return null;
  const done = items.filter(i => i.status === 'ready').length;
  const total = items.filter(i => i.status !== 'duplicate').length;

  return (
    <div className="snackbar" role="status" aria-live="polite">
      <div className="snackbar-head">
        <strong>{pending ? `Adding documents (${done}/${total})` : 'Documents added'}</strong>
        <button type="button" className="icon-button" aria-label="Close"
          onClick={() => (pending ? setHidden(true) : setItems(() => []))}>✕</button>
      </div>
      <ul>
        {items.map(i => {
          const { icon, text } = line(i);
          return <li key={i.key} className={'snack-' + i.status}><span aria-hidden="true">{icon}</span> {text}</li>;
        })}
      </ul>
      {pending && <div className="snackbar-bar"><div style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>}
    </div>
  );
}
