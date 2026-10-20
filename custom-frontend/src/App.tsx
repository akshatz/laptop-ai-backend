import { useState } from 'react';
import type { User } from './api';
import SignIn from './screens/SignIn';
import SignUp from './screens/SignUp';
import ChatScreen from './screens/ChatScreen';

// Remembered for this tab only. There's no session token yet: the backend doesn't check who
// calls the chat endpoints, so this only decides which screen to show.
const STORAGE_KEY = 'signedInUser';

function loadUser(): User | null {
  try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null'); } catch { return null; }
}

function saveUser(user: User | null) {
  try {
    if (user) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* storage blocked */ }
}

export default function App() {
  const [user, setUserState] = useState<User | null>(loadUser);
  const [screen, setScreen] = useState<'signin' | 'signup'>('signin');
  const [notice, setNotice] = useState('');
  const [prefillEmail, setPrefillEmail] = useState('');

  const setUser = (u: User | null) => { saveUser(u); setUserState(u); };

  return (
    <main>
      <h1>Laptop Dev Chat</h1>
      {user ? (
        <ChatScreen user={user} onUserChange={setUser} onSignOut={() => { setUser(null); setScreen('signin'); }} />
      ) : screen === 'signup' ? (
        <SignUp
          onVerified={email => { setPrefillEmail(email); setNotice('Email verified. You can sign in now.'); setScreen('signin'); }}
          onShowSignIn={() => { setNotice(''); setScreen('signin'); }}
        />
      ) : (
        <SignIn
          initialEmail={prefillEmail}
          notice={notice}
          onSignedIn={u => { setNotice(''); setUser(u); }}
          onShowSignUp={() => { setNotice(''); setScreen('signup'); }}
        />
      )}
    </main>
  );
}
