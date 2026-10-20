import { useEffect, useState } from 'react';
import { api, type FeedbackDashboard } from '../api';

interface Props {
  onError: (prefix: string, err: unknown) => void;
  onOpenChat: (chatId: string) => void;
}

const RANGES = [7, 30, 90, 365];
const pct = (n: number, of: number) => (of ? `${Math.round((n / of) * 100)}%` : '–');
const short = (text: string | null | undefined, n = 140) =>
  !text ? '' : text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text;

export default function FeedbackView({ onError, onOpenChat }: Props) {
  const [days, setDays] = useState(30);
  const [only, setOnly] = useState<'all' | 'likes' | 'dislikes'>('all');
  const [data, setData] = useState<FeedbackDashboard | null>(null);

  useEffect(() => {
    api.feedback(days).then(setData).catch(err => onError('Could not load feedback: ', err));
  }, [days]);

  if (!data) return <section className="documents"><p className="muted">Loading…</p></section>;

  const rated = data.likes + data.dislikes;
  const recent = data.recent.filter(f => only === 'all' || (only === 'likes' ? f.rating === 1 : f.rating === -1));
  const everyone = data.scope === 'everyone';

  return (
    <section className="documents feedback-dash">
      <div className="dash-head">
        <h2>Feedback {everyone ? '(everyone)' : '(your answers)'}</h2>
        <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Time range">
          {RANGES.map(d => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>

      <div className="tiles">
        <div className="tile"><span>Answers</span><strong>{data.answers}</strong></div>
        <div className="tile"><span>Rated</span><strong>{rated}</strong><small>{pct(rated, data.answers)} of answers</small></div>
        <div className="tile"><span>👍 Likes</span><strong>{data.likes}</strong><small>{pct(data.likes, rated)} of rated</small></div>
        <div className="tile"><span>👎 Dislikes</span><strong>{data.dislikes}</strong><small>{pct(data.dislikes, rated)} of rated</small></div>
      </div>

      <h2 className="docs-heading">By model</h2>
      {data.by_model.length === 0 ? <p className="muted">No answers in this period.</p> : (
        <table className="doc-table">
          <thead><tr><th>Model</th><th>Answers</th><th>👍</th><th>👎</th><th>Liked</th></tr></thead>
          <tbody>
            {data.by_model.map(s => (
              <tr key={s.model_id}>
                <td>{s.model_id.replace(/:latest$/, '')}</td><td>{s.answers}</td><td>{s.likes}</td><td>{s.dislikes}</td>
                <td>
                  <div className="ratio" title={`${s.likes} 👍 / ${s.dislikes} 👎`}>
                    <div className="ratio-like" style={{ width: pct(s.likes, s.likes + s.dislikes).replace('–', '0%') }} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="dash-head docs-heading">
        <h2>Rated answers</h2>
        <select value={only} onChange={e => setOnly(e.target.value as typeof only)} aria-label="Filter">
          <option value="all">All</option><option value="likes">👍 only</option><option value="dislikes">👎 only</option>
        </select>
      </div>
      {recent.length === 0 ? <p className="muted">Nothing rated yet. Use 👍 / 👎 under an answer.</p> : (
        <ul className="feedback-list">
          {recent.map(f => (
            <li key={f.message_id}>
              <div className="feedback-meta">
                <span>{f.rating === 1 ? '👍' : '👎'}</span>
                <span>{new Date(f.created_at).toLocaleString()}</span>
                <span>{f.model_id.replace(/:latest$/, '')}</span>
                {everyone && <span>{f.user_name}</span>}
                {!everyone && <button type="button" className="link" onClick={() => onOpenChat(f.chat_id)}>{f.chat_title}</button>}
              </div>
              {f.question && <div className="feedback-q">Q: {short(f.question)}</div>}
              <div className="feedback-a">{short(f.answer, 260)}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
