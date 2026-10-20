import { useEffect, useState } from 'react';
import { api, type SignedInSession } from '../api';

interface Props {
  onError: (prefix: string, err: unknown) => void;
}

const REFRESH_MS = 30_000;

function ago(iso: string, now: number) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

// "Chrome on Android" from a user agent; good enough to tell a family member's devices apart.
function device(ua: string | null | undefined) {
  if (!ua) return 'Unknown device';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
}

const stamp = (iso: string) => new Date(iso).toLocaleString();

// Admins only (laptop-backend's GET /admin/sessions): who is signed in to laptop-frontend now.
export default function AdminSessionsView({ onError }: Props) {
  const [sessions, setSessions] = useState<SignedInSession[] | null>(null);
  const [loadedAt, setLoadedAt] = useState(Date.now());

  useEffect(() => {
    const load = () => api.adminSessions()
      .then(s => { setSessions(s); setLoadedAt(Date.now()); })
      .catch(err => onError('Could not load signed-in users: ', err));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  const people = new Set((sessions ?? []).map(s => s.user_id)).size;
  const activeNow = new Set((sessions ?? []).filter(s => s.active_now).map(s => s.user_id)).size;

  return (
    <section className="documents library">
      <div className="dash-head">
        <h2>Signed-in users</h2>
        <span className="muted library-count">Updated {new Date(loadedAt).toLocaleTimeString()} · refreshes every 30 s</span>
      </div>

      {sessions === null ? <p className="muted">Loading…</p> : (
        <>
          <div className="tiles">
            <div className="tile"><span>Signed in</span><strong>{people}</strong><small>{sessions.length} session{sessions.length === 1 ? '' : 's'}</small></div>
            <div className="tile"><span>Active now</span><strong>{activeNow}</strong><small>seen in the last 5 min</small></div>
          </div>
          {sessions.length === 0 ? <p className="muted">Nobody is signed in.</p> : (
            <div className="doc-cards sessions">
              {sessions.map(s => (
                <article key={s.id} className="doc-card">
                  <div className="doc-card-top">
                    <strong className="session-name">{s.name}{s.is_you && <span className="muted"> (you)</span>}</strong>
                    <span className={'chip ' + (s.active_now ? 'ready' : '')}>{s.active_now ? '● Active now' : 'Idle'}</span>
                  </div>
                  <span className="muted session-email">{s.email}{s.role === 'admin' ? ' · admin' : ''}</span>
                  <dl className="doc-card-meta">
                    <dt>Signed in</dt><dd><time dateTime={s.signed_in_at}>{stamp(s.signed_in_at)}</time> <span className="muted">({ago(s.signed_in_at, loadedAt)})</span></dd>
                    <dt>Last seen</dt><dd><time dateTime={s.last_seen_at}>{stamp(s.last_seen_at)}</time> <span className="muted">({ago(s.last_seen_at, loadedAt)})</span></dd>
                    <dt>Expires</dt><dd><time dateTime={s.expires_at}>{stamp(s.expires_at)}</time></dd>
                    <dt>Device</dt><dd title={s.user_agent ?? undefined}>{device(s.user_agent)}</dd>
                  </dl>
                </article>
              ))}
            </div>
          )}
          <p className="muted library-count">Sign-ins from before this list existed show up after that person signs in again.</p>
        </>
      )}
    </section>
  );
}
