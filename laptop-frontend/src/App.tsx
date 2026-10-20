import { useEffect, useState } from 'react';
import { api, HttpError, SIGN_IN_URL, type User } from './api';
import ChatScreen from './screens/ChatScreen';

// Set by laptop-backend's /auth/callback when sign-in didn't complete.
const SIGNIN_ERRORS: Record<string, string> = {
  failed: 'Sign-in did not complete. Please try again.',
  deactivated: 'This account has been deactivated.',
  unavailable: 'Sign-in is not set up on the server yet.',
};

// Signed out → straight to authentik (like Open WebUI's OAUTH_AUTO_REDIRECT); with a live authentik
// session that signs in without a page. Not after a sign-in error, and not twice within 30 s, so a
// callback that keeps failing shows the Sign in button instead of looping.
const REDIRECT_KEY = 'signin.autoRedirectAt';
function autoSignIn(): boolean {
  try {
    const last = Number(sessionStorage.getItem(REDIRECT_KEY) || 0);
    if (Date.now() - last < 30_000) return false;
    sessionStorage.setItem(REDIRECT_KEY, String(Date.now()));
  } catch { /* storage blocked: still redirect once per page load */ }
  location.replace(SIGN_IN_URL);
  return true;
}

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
      .then(u => { setUser(u); try { sessionStorage.removeItem(REDIRECT_KEY); } catch { /* ignore */ } setLoading(false); })
      .catch(err => {
        // 401: not signed in; go to authentik unless that just failed (then the dashboard asks).
        if (err instanceof HttpError && err.status === 401) { if (!signin && autoSignIn()) return; }
        else setError('Could not reach the server: ' + err.message);
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

  if (loading) return <main><p>Signing in…</p></main>;

  return <ChatScreen user={user} notice={error} onSignOut={signOut} onSessionExpired={() => setUser(null)} />;
}
