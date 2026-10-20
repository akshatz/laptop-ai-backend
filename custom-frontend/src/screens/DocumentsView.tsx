import { useEffect, useRef, useState, type DragEvent } from 'react';
import { api, documentUrl, type Chat, type ChatDocument } from '../api';

interface Props {
  chat: Chat | null;
  ensureChat: () => Promise<Chat | null>;
  onError: (prefix: string, err: unknown) => void;
  onOpenChat: (chatId: string) => void;
  onBack: () => void;
}

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
// Keep in sync with custom-backend/file_text.py.
const ACCEPT = '.pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,.xml,.log,.yaml,.yml';

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

export default function DocumentsView({ chat, ensureChat, onError, onOpenChat, onBack }: Props) {
  const [picked, setPicked] = useState<File[]>([]);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [dragging, setDragging] = useState(false);
  const [docs, setDocs] = useState<ChatDocument[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const reload = () => api.documents().then(setDocs).catch(err => onError('Could not load your documents: ', err));
  useEffect(() => { reload(); }, []);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const all = [...picked, ...Array.from(list)];
    const tooBig = all.find(f => f.size > MAX_BYTES);
    if (tooBig) { setNote(`${tooBig.name} is larger than 10 MB.`); return; }
    if (all.length > MAX_FILES) { setNote(`At most ${MAX_FILES} files at a time.`); return; }
    setNote('');
    setPicked(all);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  async function upload() {
    if (!picked.length && !pasted.trim()) { setNote('Choose a file first.'); return; }
    setBusy(true);
    setNote('');
    const target = await ensureChat();
    if (!target) { setBusy(false); return; }
    try {
      let added = 0;
      if (picked.length) added += (await api.upload(target.id, picked)).documents_ingested;
      if (pasted.trim()) added += (await api.ingest(target.id, [pasted.trim()])).documents_ingested;
      setPicked([]);
      setPasted('');
      setNote(`Added ${added} document(s) to “${target.title}”. Ask about them in the chat.`);
      reload();
    } catch (err) {
      onError('Upload failed: ', err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="documents">
      <h2>Add documents</h2>
      <p className="muted">
        Files are read and added as context for {chat ? <strong>{chat.title}</strong> : 'a new chat'}.
        PDF (with selectable text), Word (.docx) or text files; up to {MAX_FILES} at a time, 10 MB each.
      </p>

      <div className={'dropzone' + (dragging ? ' dragging' : '')}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)} onDrop={onDrop}
        onClick={() => inputRef.current?.click()} role="button" tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}>
        <div className="dropzone-icon">⬆</div>
        <div><strong>Choose files</strong> or drag them here</div>
        <input ref={inputRef} type="file" multiple accept={ACCEPT} hidden
          onChange={e => { addFiles(e.target.files); e.target.value = ''; }} />
      </div>

      {picked.length > 0 && (
        <ul className="picked">
          {picked.map((f, i) => (
            <li key={i}>
              📄 {f.name} <span className="muted">{size(f.size)}</span>
              <button type="button" className="icon-button" aria-label={`Remove ${f.name}`}
                onClick={() => setPicked(p => p.filter((_, j) => j !== i))}>✕</button>
            </li>
          ))}
        </ul>
      )}

      <details className="paste">
        <summary>Or paste text instead</summary>
        <textarea value={pasted} onChange={e => setPasted(e.target.value)} placeholder="Paste text here" />
      </details>

      {note && <p className="note">{note}</p>}
      <div className="actions">
        <button type="button" className="primary" onClick={upload} disabled={busy}>{busy ? 'Adding…' : 'Add to chat'}</button>
        {chat && <button type="button" onClick={onBack}>Back to chat</button>}
      </div>

      <h2 className="docs-heading">My documents</h2>
      {docs === null ? <p className="muted">Loading…</p> : docs.length === 0 ? (
        <p className="muted">You haven't added any documents yet.</p>
      ) : (
        <table className="doc-table">
          <thead><tr><th>Name</th><th>Chat</th><th>Size</th><th>Added</th></tr></thead>
          <tbody>
            {docs.map(d => (
              <tr key={d.id}>
                <td><a href={documentUrl(d.id)} target="_blank" rel="noreferrer">📄 {d.filename}</a></td>
                <td><button type="button" className="link" onClick={() => onOpenChat(d.chat_id)}>{d.chat_title}</button></td>
                <td>{size(d.size_bytes)}</td>
                <td>{new Date(d.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
