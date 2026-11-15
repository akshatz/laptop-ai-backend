import { type ChatDocument } from '../api';
import VersionTimeline from './VersionTimeline';

interface Props {
  // Every upload of one file name by one person, newest first.
  versions: ChatDocument[];
  onClose: () => void;
  onOpenChat: (chatId: string) => void;
}

// The right-hand panel opened by "🕘 N versions" on a card in All documents.
export default function DocumentHistory({ versions, onClose, onOpenChat }: Props) {
  return (
    <aside className="history-panel" aria-label={`History of ${versions[0].filename}`}>
      <div className="history-head">
        <div>
          <h3 title={versions[0].filename}>{versions[0].filename}</h3>
          <p className="muted">{versions.length} uploads</p>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close history">✕</button>
      </div>
      <VersionTimeline versions={versions} onOpenChat={onOpenChat} />
    </aside>
  );
}
