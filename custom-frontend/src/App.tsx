import { useEffect, useState } from 'react';
import { api, HttpError, SIGN_IN_URL, type User } from './api';
import ChatScreen from './screens/ChatScreen';

// Set by custom-backend's /auth/callback when sign-in didn't complete.
const SIGNIN_ERRORS: Record<string, string> = {
  failed: 'Sign-in did not complete. Please try again.',
  deactivated: 'This account has been deactivated.',
  unavailable: 'Sign-in is not set up on the server yet.',
};

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const signin = params.get('signin');
    if (signin) {
      setError(SIGNIN_ERRORS[signin] ?? SIGNIN_ERRORS.failed);
      history.replaceState(null, '', location.pathname);
    }
    api.me()
      .then(u => { setUser(u); setLoading(false); })
      .catch(err => {
        if (err instanceof HttpError && err.status === 401 && !signin) {
          // Not signed in here: go to authentik straight away. Anyone already signed in there
          // (e.g. to Open WebUI) comes right back signed in. After a failed attempt (?signin=…)
          // the button is shown instead, so this can't loop.
          location.replace(SIGN_IN_URL);
          return;
        }
        if (!(err instanceof HttpError && err.status === 401)) setError('Could not reach the server: ' + err.message);
        setLoading(false);
      });
  }, []);

  async function signOut() {
    try {
      const { authentik_logout_url } = await api.logout();
      // Also end authentik's session, or the next sign-in would go straight through.
      if (authentik_logout_url) { location.href = authentik_logout_url; return; }
    } catch { /* the cookie may already be gone */ }
    setUser(null);
  }

  if (loading) return <main><p>Loading…</p></main>;

  return (
    <main>
      <h1>Laptop Dev Chat</h1>
      {user ? (
        <ChatScreen user={user} onSignOut={signOut} onSessionExpired={() => setUser(null)} />
      ) : (
        <div className="card signin">
          {error && <p className="status">{error}</p>}
          <p>Sign in with your family account, the same one you use for Open WebUI.</p>
          <a className="button" href={SIGN_IN_URL}>Sign in</a>
        </div>
      )}
    </main>
  );
}
