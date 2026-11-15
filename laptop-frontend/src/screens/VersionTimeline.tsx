import { documentUrl, type ChatDocument } from '../api';

interface Props {
  // Every upload of one file name by one person, newest first.
  versions: ChatDocument[];
  onOpenChat: (chatId: string) => void;
}

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

// A file's uploads across all chats, like `git log` for one file: newest at the top, each marked
// as a change or as the same content as the upload before it (sha256). Used by the right-hand
// panel in All documents (DocumentHistory) and by the Versions page (VersionsView).
export default function VersionTimeline({ versions, onOpenChat }: Props) {
  const oldestFirst = [...versions].reverse();
  const number = new Map(oldestFirst.map((d, i) => [d.id, i + 1]));
  const sameAsPrevious = new Map(oldestFirst.map((d, i) => [d.id, i > 0 && !!d.sha256 && d.sha256 === oldestFirst[i - 1].sha256]));

  return (
    <ol className="history-log">
      {versions.map((d, i) => (
        <li key={d.id} className={i === 0 ? 'latest' : ''}>
          <span className="history-dot" aria-hidden="true" />
          <div className="history-entry">
            <div className="history-title">
              <a href={documentUrl(d.id)} target="_blank" rel="noreferrer">v{number.get(d.id)}</a>
              {i === 0 && <span className="chip ready">latest</span>}
              <span className={'chip ' + (sameAsPrevious.get(d.id) ? 'replaced' : 'version')}>
                {number.get(d.id) === 1 ? 'first upload' : sameAsPrevious.get(d.id) ? 'same content' : 'changed'}
              </span>
            </div>
            <div className="muted history-meta">
              {new Date(d.created_at).toLocaleString()} · {size(d.size_bytes)}
              {d.sha256 && <> · <code title={d.sha256}>{d.sha256.slice(0, 7)}</code></>}
            </div>
            {/* Other people's chats stay private: their uploads come without one (admins get it). */}
            {d.chat_title != null && (
              <div className="history-meta">
                Chat:{' '}
                {d.mine
                  ? <button type="button" className="link" onClick={() => { if (d.chat_id) onOpenChat(d.chat_id); }}>{d.chat_title}</button>
                  : d.chat_title}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
