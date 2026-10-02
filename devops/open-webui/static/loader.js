// Mounted over Open WebUI's empty /app/build/static/loader.js (copied to /static/loader.js at
// startup, and loaded by every page). Adds a "Sign up" button under "Continue with authentik" on
// the sign-in page, linking to authentik's self-enrollment flow
// (authentik/blueprints/self-enrollment.yaml) on the same host, port 9443 (Caddy).
// After sign-up authentik follows next= to the Open WebUI application's launch URL; it only
// accepts relative next= values, hence the /application/launch/ hop instead of a direct URL.
(() => {
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
})();
