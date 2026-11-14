import { useEffect, useMemo, useState } from 'react';
import { api, documentUrl, type ChatDocument } from '../api';

interface Props {
  // Changes when an upload or embedding finishes, or a document is deleted.
  reloadKey: number;
  onOpenChat: (chatId: string) => void;
}

const isImage = (name: string) => /\.(png|jpe?g|gif|webp)$/i.test(name);

function icon(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith('.pdf')) return '📕';
  if (n.endsWith('.docx')) return '📘';
  if (isImage(n)) return '🖼️';
  return '📄';
}

// The sidebar's Documents section: each of your files once (newest upload first), expanding to
// its versions across chats like a git log: v1…vN by upload time, each marked as the first
// upload, a change, or the same content as the one before (sha256).
export default function SidebarDocuments({ reloadKey, onOpenChat }: Props) {
  const [docs, setDocs] = useState<ChatDocument[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.documents().then(setDocs).catch(() => setDocs([]));
  }, [reloadKey]);

  // filename → uploads, newest first (GET /documents is newest first).
  const files = useMemo(() => {
    const map = new Map<string, ChatDocument[]>();
    for (const d of docs) map.set(d.filename, [...(map.get(d.filename) ?? []), d]);
    return [...map.entries()];
  }, [docs]);

  if (!files.length) return null;

  return (
    <>
      <div className="side-heading">Documents</div>
      <nav className="side-docs" aria-label="Your documents and their versions">
        {files.map(([name, versions]) => {
          const oldestFirst = [...versions].reverse();
          const label = new Map(oldestFirst.map((d, i) => [d.id, {
            n: i + 1,
            kind: i === 0 ? 'first' : d.sha256 && d.sha256 === oldestFirst[i - 1].sha256 ? 'same' : 'changed',
          }]));
          const expanded = open === name;
          return (
            <div key={name} className="side-doc">
              <button type="button" className="side-doc-file" aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : name)} title={name}>
                <span className="side-doc-caret" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
                <span aria-hidden="true">{icon(name)}</span>
                <span className="side-doc-name">{name}</span>
                {versions.length > 1 && <span className="side-doc-count">{versions.length}</span>}
              </button>
              {expanded && (
                <ol className="side-doc-versions">
                  {versions.map((d, i) => {
                    const { n, kind } = label.get(d.id)!;
                    return (
                      <li key={d.id} className={i === 0 ? 'latest' : ''}>
                        <a href={documentUrl(d.id)} target="_blank" rel="noreferrer" title="Open this version">
                          v{n}{i === 0 ? ' · latest' : ''}
                        </a>
                        <small>
                          {kind === 'first' ? 'first upload' : kind === 'same' ? 'same content' : 'changed'}
                          {' · '}{new Date(d.created_at).toLocaleDateString()}
                        </small>
                        <button type="button" className="link side-doc-chat" onClick={() => onOpenChat(d.chat_id)}
                          title={d.chat_title}>{d.chat_title}</button>
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          );
        })}
      </nav>
    </>
  );
}
