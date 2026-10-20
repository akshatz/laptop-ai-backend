import { useState, type FormEvent } from 'react';
import { api } from '../api';

interface Props {
  onVerified: (email: string) => void;
  onShowSignIn: () => void;
}

export default function SignUp({ onVerified, onShowSignIn }: Props) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [token, setToken] = useState('');
  const [created, setCreated] = useState(false);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function createAccount(e: FormEvent) {
    e.preventDefault();
    if (password !== password2) { setStatus('The two passwords differ.'); return; }
    setStatus('');
    setBusy(true);
    try {
      await api.signUp(name.trim(), email.trim(), password);
      setCreated(true);
      setPassword('');
      setPassword2('');
      setStatus('Account created. Check your email for the verification token and paste it below.');
    } catch (err) {
      setStatus('Sign-up failed: ' + (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    setStatus('');
    setBusy(true);
    try {
      const user = await api.verifyEmail(token.trim());
      onVerified(user.email);
    } catch (err) {
      setStatus('Verification failed: ' + (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Sign up</h2>
      {status && <p className="status">{status}</p>}
      {!created ? (
        <form onSubmit={createAccount}>
          <label>Name
            <input autoComplete="name" required value={name} onChange={e => setName(e.target.value)} />
          </label>
          <label>Email
            <input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} />
          </label>
          <label>Password
            <input type="password" autoComplete="new-password" required value={password}
              onChange={e => setPassword(e.target.value)} />
          </label>
          <label>Repeat password
            <input type="password" autoComplete="new-password" required value={password2}
              onChange={e => setPassword2(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>Create account</button>
          <button type="button" className="link" onClick={() => { setCreated(true); setStatus(''); }}>
            I already have a verification token
          </button>
        </form>
      ) : (
        <form onSubmit={verify}>
          <label>Verification token (sent by email)
            <input autoComplete="off" required autoFocus value={token} onChange={e => setToken(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>Verify email</button>
        </form>
      )}
      <p>Already have an account? <button type="button" className="link" onClick={onShowSignIn}>Sign in</button></p>
    </div>
  );
}
