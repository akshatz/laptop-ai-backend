// Mounted over Open WebUI's empty /app/build/static/loader.js (copied to /static/loader.js at
// startup, and loaded by every page). Four things:
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

	if (hasToken()) {
		const headers = {};
		try {
			if (localStorage.token) headers.Authorization = `Bearer ${localStorage.token}`;
		} catch (e) {}
		fetch('/api/v1/auths/', { credentials: 'include', headers })
			.then((r) => (r.ok ? r.json() : null))
			.then((user) => {
				if (user && user.role !== 'admin') restrictRegularUser();
			})
			.catch(() => {});
	}
})();
