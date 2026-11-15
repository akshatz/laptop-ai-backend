import { useEffect, useState } from 'react';

// Current local time (and date), centred in the top bar; ticks every second.
export default function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <time className="clock" dateTime={now.toISOString()} title={now.toLocaleDateString(undefined, { dateStyle: 'full' })}>
      {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
    </time>
  );
}
