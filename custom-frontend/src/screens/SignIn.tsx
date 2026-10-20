import { useState, type FormEvent } from 'react';
import { api, type User } from '../api';

interface Props {
  initialEmail: string;
  notice: string;
  onSignedIn: (user: User) => void;
  onShowSignUp: () => void;
}

export default function SignIn({ initialEmail, notice, onSignedIn, onShowSignUp }: Props) {
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [otpPending, setOtpPending] = useState(false);
  const [status, setStatus] = useState(notice);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setStatus('');
    setBusy(true);
    try {
      if (otpPending) {
        onSignedIn(await api.verifyOtp(email.trim(), otp.trim()));
        return;
      }
      const result = await api.login(email.trim(), password);
      if (result.password_reset_required) {
        setStatus('Your password has expired. A reset code was emailed to you.');
      } else if (result.mfa_required) {
        setOtpPending(true);
        setStatus('A one-time code was emailed to you. Enter it below.');
      } else if (result.user) {
        onSignedIn(result.user);
      }
    } catch (err) {
      setStatus('Sign-in failed: ' + (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Sign in</h2>
      {status && <p className="status">{status}</p>}
      <label>Email
        <input type="email" autoComplete="username" required value={email}
          disabled={otpPending} onChange={e => setEmail(e.target.value)} />
      </label>
      {!otpPending && (
        <label>Password
          <input type="password" autoComplete="current-password" required value={password}
            onChange={e => setPassword(e.target.value)} />
        </label>
      )}
      {otpPending && (
        <label>One-time code (sent by email)
          <input inputMode="numeric" autoComplete="one-time-code" required autoFocus value={otp}
            onChange={e => setOtp(e.target.value)} />
        </label>
      )}
      <button type="submit" disabled={busy}>{otpPending ? 'Verify code' : 'Sign in'}</button>
      {otpPending && (
        <button type="button" className="link" onClick={() => { setOtpPending(false); setOtp(''); setStatus(''); }}>
          Start over
        </button>
      )}
      <p>New here? <button type="button" className="link" onClick={onShowSignUp}>Create an account</button></p>
    </form>
  );
}
