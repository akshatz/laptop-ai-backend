// Mounted over Open WebUI's empty /app/build/static/loader.js (copied to /static/loader.js at
// startup, and loaded by every page). Six things:
//
// 1. Signed-out visitors go straight to authentik. OAUTH_AUTO_REDIRECT does this too, but only
//    after the SvelteKit app has started and rendered its sign-in page, so "Continue with
//    authentik" flashed for a second or two. This runs before that: it keeps the page hidden
//    behind Open WebUI's splash screen, checks the same conditions as Open WebUI's auth page
//    (src/routes/auth/+page.svelte — auto_redirect on, a single OAuth provider, no login form, no
//    LDAP/trusted-header, not onboarding, no token; skipped after logout and on ?error= / ?form=),
//    and then redirects. If any condition fails, the page is shown as usual.
//
// 2. A "Sign up" button under "Continue with authentik" on the sign-in page (still shown after
//    logout or a sign-in error), linking to authentik's self-enrollment flow
//    (authentik/blueprints/self-enrollment.yaml) on the same host, port 9443 (Caddy). After sign-up
//    authentik follows next= to the Open WebUI application's launch URL; it only accepts relative
//    next= values, hence the /application/launch/ hop instead of a direct URL.
//
// 3. The Settings dialog closes after a successful Save, back to the chat underneath (Open WebUI
//    leaves it open). See the comment at that section.
//
// 4. For anyone but admins: no Info button under answers (Open WebUI has no permission for it), and
//    no Regenerate button once an answer has 3 versions.
//
// 5. For anyone but admins: signed out after 3 hours without activity, from authentik too.
//
// 6. "My usage" and "My feedback" links in the sidebar, under Search, to the KPI Dashboard's
//    ?view=me page and Feedback Review's ?mine=1 page.
(() => {
	// ---- 1. early SSO redirect ----------------------------------------------------------------
	const params = new URLSearchParams(location.search);
	const onAuthPage = location.pathname.startsWith('/auth');
	const hasToken = () => {
		try {
			if (localStorage.token) return true;
		} catch (e) {}
		return document.cookie.split('; ').some((c) => c.startsWith('token='));
	};
	const suppressed =
		onAuthPage &&
		(params.get('state') === 'logout' || params.has('error') || params.has('form'));

	if (!hasToken() && !suppressed) {
		// Everything except the splash screen stays invisible until we know where we're going.
		const hide = document.createElement('style');
		hide.textContent = 'body > :not(#splash-screen) { visibility: hidden !important; }';
		document.head.appendChild(hide);
		const show = () => hide.remove();

		fetch('/api/config', { credentials: 'include' })
			.then((r) => (r.ok ? r.json() : null))
			.then((config) => {
				const providers = Object.keys(config?.oauth?.providers ?? {});
				const features = config?.features ?? {};
				const redirect =
					config?.oauth?.auto_redirect &&
					providers.length === 1 &&
					features.auth !== false &&
					features.enable_login_form === false &&
					!features.enable_ldap &&
					!features.auth_trusted_header &&
					!config?.onboarding &&
					!hasToken();
				if (!redirect) return show();
				// Where to land after sign-in (Open WebUI reads this back in its auth page).
				const target = onAuthPage
					? params.get('redirect')
					: location.pathname + location.search;
				try {
					if (target && target !== '/') localStorage.setItem('redirectPath', target);
				} catch (e) {}
				location.replace(`/oauth/${providers[0]}/login`);
				// If navigation is blocked or slow, don't leave a blank page forever.
				setTimeout(show, 5000);
			})
			.catch(show);
	}

	// ---- 2. "Sign up" button --------------------------------------------------------------------
	const BUTTON_ID = 'authentik-signup-button';
	const signupUrl =
		`https://${location.hostname}:9443/if/flow/self-enrollment/` +
		`?next=${encodeURIComponent('/application/launch/open-webui/')}`;

	const addButton = () => {
		if (!location.pathname.startsWith('/auth') || document.getElementById(BUTTON_ID)) return;
		const ssoButton = [...document.querySelectorAll('button')].find((b) =>
			b.textContent.includes('Continue with authentik')
		);
		if (!ssoButton) return;

		const signup = document.createElement('a');
		signup.id = BUTTON_ID;
		signup.href = signupUrl;
		signup.className = ssoButton.className;
		signup.textContent = 'Sign up';
		// The SSO button is a flex row centred by its classes; keep the link looking the same.
		signup.style.marginTop = '0.5rem';
		signup.style.textDecoration = 'none';
		ssoButton.insertAdjacentElement('afterend', signup);
	};

	// Open WebUI is a SvelteKit SPA: the sign-in page renders after this script runs and can be
	// re-rendered on client-side navigation, so watch the DOM rather than running once.
	new MutationObserver(addButton).observe(document.documentElement, {
		childList: true,
		subtree: true
	});
	addButton();

	// ---- 3. close Settings after a successful Save ----------------------------------------------
	// The Settings dialog (src/lib/components/chat/SettingsModal.svelte, user and admin tabs alike)
	// has a tab list, #settings-tabs-container, whose first button is "Back". Every tab's Save
	// submits a <form> in the dialog and, once saved, shows a svelte-sonner toast with
	// data-type="success". So a form submitted there arms a short window, and the next new success
	// toast clicks "Back", which closes the dialog the normal way. An error toast disarms it, so a
	// failed save stays open with its message. Forms in dialogs opened from Settings (e.g. editing an
	// Ollama connection) don't arm it: their dialog has no tab list.
	const SAVE_WINDOW_MS = 15000;
	let closeOnSaveUntil = 0;
	const seenToasts = new WeakSet();

	document.addEventListener(
		'submit',
		(event) => {
			const dialog = event.target.closest?.('[role="dialog"]');
			if (!dialog?.querySelector('#settings-tabs-container')) return;
			// Toasts already on screen (e.g. from an earlier save) aren't this save's result.
			document.querySelectorAll('[data-sonner-toast]').forEach((t) => seenToasts.add(t));
			closeOnSaveUntil = Date.now() + SAVE_WINDOW_MS;
		},
		true
	);

	new MutationObserver(() => {
		if (Date.now() > closeOnSaveUntil) return;
		for (const toast of document.querySelectorAll('[data-sonner-toast][data-type]')) {
			if (seenToasts.has(toast)) continue;
			const type = toast.getAttribute('data-type');
			if (type !== 'success' && type !== 'error') continue;
			seenToasts.add(toast);
			closeOnSaveUntil = 0;
			if (type === 'success') document.querySelector('#settings-tabs-container button')?.click();
			return;
		}
	}).observe(document.documentElement, {
		childList: true,
		subtree: true,
		attributes: true,
		attributeFilter: ['data-type']
	});

	// ---- 4. regular users: no Info button, Regenerate only up to 3 answers -----------------------
	// Both apply once /api/v1/auths/ says the signed-in user isn't an admin.
	// Info: the (i) button under each answer (id info-<message id>, ResponseMessage.svelte) shows
	// token counts and timings. Edit, Read Aloud and Fork are hidden through permissions (chat.edit,
	// chat.tts, chat.import in user.permissions), but Info has none, so CSS hides it.
	// Regenerate: the Regenerate Limit Function (functions/regenerate_limit.py) refuses a question's
	// 4th answer, but Open WebUI adds the new answer before asking the server, so the refusal would
	// show up as a "4/4" answer. So the button (class regenerate-response-button) is disabled and
	// dimmed as soon as the "x/N" counter in its row (.buttons) reaches MAX_ANSWERS, which must
	// equal the Function's max_answers Valve. The Function stays the actual limit.
	const MAX_ANSWERS = 3;
	const COUNTER = /^\d+\/(\d+)$/;

	const answersIn = (row) => {
		// While the counter is being edited (double-click), it's an input with max = N.
		const input = row.querySelector('input[id^="message-index-input-"]');
		if (input) return Number(input.max) || 1;
		for (const el of row.querySelectorAll('div')) {
			const match = el.childElementCount === 0 && el.textContent.trim().match(COUNTER);
			if (match) return Number(match[1]);
		}
		return 1; // no counter: a single answer
	};

	const limitRegenerate = () => {
		for (const button of document.querySelectorAll('.buttons .regenerate-response-button')) {
			const limited = answersIn(button.closest('.buttons')) >= MAX_ANSWERS;
			button.disabled = limited;
			button.style.opacity = limited ? '0.3' : '';
			button.style.cursor = limited ? 'not-allowed' : '';
			button.title = limited ? `Up to ${MAX_ANSWERS} answers per question` : '';
		}
	};

	const restrictRegularUser = () => {
		const style = document.createElement('style');
		style.textContent = '[id^="info-"] { display: none !important; }';
		document.head.appendChild(style);

		// Answers stream in and counters change, so re-check after DOM changes, at most once a frame.
		let queued = false;
		new MutationObserver(() => {
			if (queued) return;
			queued = true;
			requestAnimationFrame(() => {
				queued = false;
				limitRegenerate();
			});
		}).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
		limitRegenerate();
	};

	// ---- 5. regular users: signed out after 3 hours without activity ----------------------------
	// Open WebUI has no idle timeout (its token lasts auth.jwt_expiry, 4 weeks). Activity is a pointer,
	// key, wheel or touch event in any Open WebUI tab of the browser; its time is kept in localStorage
	// so tabs share it, and the token's own issue time counts too, so a fresh sign-in always starts a
	// new 3 hours. Checked every minute, when a tab comes back into view, and on the first activity
	// after a pause (moving the mouse after a night away signs out rather than counting as activity).
	// Signing out of Open WebUI alone wouldn't do: authentik's session would still be there, and
	// section 1 would sign the user straight back in. So it then runs authentik's logout flow, which
	// ends at "/" and so, through Caddy's redirect and section 1, at authentik's sign-in page.
	// Client-side only: the token itself stays valid until it expires.
	const IDLE_LIMIT_MS = 3 * 60 * 60 * 1000;
	const IDLE_KEY = 'idleSignout.lastActivity';
	const LOGOUT_URL = `https://${location.hostname}:9443/flows/-/default/invalidation/`;
	let signingOut = false;

	const signOut = (token) => {
		if (signingOut) return;
		signingOut = true;
		document.documentElement.style.visibility = 'hidden';
		const toAuthentik = () => location.replace(LOGOUT_URL);
		if (!token) return toAuthentik();
		try {
			localStorage.removeItem('token'); // other tabs follow (storage event below)
		} catch (e) {}
		// credentials: 'include' so the response's cookie deletions apply.
		fetch('/api/v1/auths/signout', {
			method: 'POST',
			credentials: 'include',
			headers: { Authorization: `Bearer ${token}` }
		})
			.catch(() => {})
			.finally(toAuthentik);
	};

	const watchIdle = (token) => {
		let issuedAt = 0;
		try {
			const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
			issuedAt = (JSON.parse(atob(payload)).iat || 0) * 1000;
		} catch (e) {}
		const stored = () => {
			try {
				return Number(localStorage.getItem(IDLE_KEY)) || 0;
			} catch (e) {
				return 0;
			}
		};
		const markActive = () => {
			try {
				localStorage.setItem(IDLE_KEY, String(Date.now()));
			} catch (e) {}
		};
		const idleTooLong = () => Date.now() - Math.max(stored(), issuedAt) >= IDLE_LIMIT_MS;
		// Someone else may sign in on this browser later; this tab then stops watching.
		const current = () => {
			try {
				return localStorage.token === token;
			} catch (e) {
				return false;
			}
		};
		const check = () => {
			if (!signingOut && current() && idleTooLong()) signOut(token);
		};

		if (!stored() && !issuedAt) markActive(); // nothing to go by yet: start counting now
		let lastMark = 0;
		const onActivity = () => {
			const now = Date.now();
			if (signingOut || now - lastMark < 60 * 1000 || !current()) return;
			if (idleTooLong()) return signOut(token);
			lastMark = now;
			markActive();
		};
		for (const type of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
			window.addEventListener(type, onActivity, { capture: true, passive: true });
		}
		check();
		setInterval(check, 60 * 1000);
		document.addEventListener('visibilitychange', () => {
			if (document.visibilityState === 'visible') check();
		});
		// Another tab signed out for inactivity: follow it to authentik's logout. (Open WebUI's own Sign
		// out removes the token too, but then the last activity is recent.)
		window.addEventListener('storage', (event) => {
			if (event.key === 'token' && event.oldValue === token && !event.newValue && idleTooLong()) {
				signOut(null);
			}
		});
	};

	// ---- sections 4 and 5 start once /api/v1/auths/ says the user isn't an admin ------------------
	// Asked with localStorage's token only, never the `token` cookie: /api/v1/auths/ re-sets that
	// cookie as HttpOnly, and right after an SSO sign-in the cookie is where Open WebUI's sign-in page
	// reads the new token from (with JavaScript). Sending the cookie then hid the token from the page,
	// which went back to authentik, which signed the user straight in again: an endless loop.
	// credentials: 'omit' also makes the browser ignore the response's Set-Cookie.
	const checkRole = () => {
		let token;
		try {
			token = localStorage.token;
		} catch (e) {
			return true; // no localStorage: nothing to wait for
		}
		if (!token) return false;
		fetch('/api/v1/auths/', { credentials: 'omit', headers: { Authorization: `Bearer ${token}` } })
			.then((r) => (r.ok ? r.json() : null))
			.then((user) => {
				if (!user || user.role === 'admin') return;
				restrictRegularUser();
				watchIdle(token);
			})
			.catch(() => {});
		return true;
	};
	// After an SSO sign-in the token reaches localStorage only after this script has run, and the app
	// then moves on to the chat without reloading the page, so keep looking until it's there.
	if (!checkRole()) {
		const timer = setInterval(() => checkRole() && clearInterval(timer), 1000);
	}

	// ---- 6. "My usage" in the sidebar -----------------------------------------------------------
	// A link under Search to the KPI Dashboard Function's own-numbers page (functions/kpi_dashboard.py,
	// ?view=me, open to every signed-in user). It copies the Search button's classes so it matches
	// in light and dark mode. data-sveltekit-reload makes it a normal page load: /api/v1/kpi isn't
	// an app route. The sidebar is re-rendered when it's toggled, so watch the DOM.
	// "My feedback" follows it: the Feedback Review Function's page (functions/feedback_review.py)
	// with only the ratings the person gave themselves.
	const svg = (d) =>
		`<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="size-4"><path stroke-linecap="round" stroke-linejoin="round" d="${d}"/></svg>`;
	const SIDEBAR_LINKS = [
		{ id: 'sidebar-my-usage', href: '/api/v1/kpi?view=me', label: 'My usage', icon: svg('M3 3v18h18M7 15l4-4 3 3 5-6') },
		{
			id: 'sidebar-my-feedback',
			href: '/api/v1/feedback-review?rating=all&mine=1',
			label: 'My feedback',
			icon: svg('M7 10v11H3V10h4zm0 0l4-8a3 3 0 013 3v4h5a2 2 0 012 2.3l-1.4 8A2 2 0 0117.6 21H7')
		}
	];

	const addUsageLink = () => {
		const search = document.getElementById('sidebar-search-button');
		if (!search?.parentElement) return;
		let after = search.parentElement;
		for (const { id, href, label, icon } of SIDEBAR_LINKS) {
			const existing = document.getElementById(id);
			if (existing) {
				after = existing.parentElement;
				continue;
			}
			const row = document.createElement('div');
			row.className = search.parentElement.className;
			const link = document.createElement('a');
			link.id = id;
			link.href = href;
			link.setAttribute('data-sveltekit-reload', '');
			link.draggable = false;
			link.className = search.className;
			link.innerHTML =
				`<div class="self-center flex size-4 shrink-0 items-center justify-center">${icon}</div>` +
				`<div class="flex flex-1 self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">${label}</div></div>`;
			row.appendChild(link);
			after.after(row);
			after = row;
		}
	};

	let usageQueued = false;
	new MutationObserver(() => {
		if (usageQueued) return;
		usageQueued = true;
		requestAnimationFrame(() => {
			usageQueued = false;
			addUsageLink();
		});
	}).observe(document.documentElement, { childList: true, subtree: true });
})();
