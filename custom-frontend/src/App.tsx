import { useEffect, useState } from 'react';
import { api, HttpError, type User } from './api';
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
      .then(setUser)
      // 401: not signed in; the dashboard then asks to sign in.
      .catch(err => { if (!(err instanceof HttpError && err.status === 401)) setError('Could not reach the server: ' + err.message); })
      .finally(() => setLoading(false));
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

  return <ChatScreen user={user} notice={error} onSignOut={signOut} onSessionExpired={() => setUser(null)} />;
}
