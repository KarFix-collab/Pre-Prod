/**
 * Supabase Auth Client Integration
 * Handles email+password and Google OAuth authentication via Supabase Auth.
 *
 * Drop-in replacement for neon-auth.js.
 * Exposes window.supabaseAuth (and window.neonAuth as a legacy alias).
 *
 * Requires the Supabase JS v2 CDN script to be loaded before this file:
 *   <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
 *
 * Reads project config from meta tags injected by layout.html:
 *   <meta name="supabase-url"      content="{{ config.get('SUPABASE_URL') }}">
 *   <meta name="supabase-anon-key" content="{{ config.get('SUPABASE_ANON_KEY') }}">
 */

class SupabaseAuthClient {
    constructor(supabaseUrl, supabaseAnonKey) {
        if (!supabaseUrl || !supabaseAnonKey) {
            console.warn('SupabaseAuthClient: missing URL or anon key — auth will not function.');
            this._client = null;
            this._handlingCallback = false;
            return;
        }

        // Keep the client compatible with both the current implicit flow used by
        // the existing auth screens and Supabase's newer PKCE code redirects.
        // The callback helpers below handle both URL fragments and code exchange.
        this._client = supabase.createClient(supabaseUrl, supabaseAnonKey, {
            auth: { flowType: 'implicit' },
        });
        this._handlingCallback = false;
        this._signupInProgress = false;
    }

    get client() {
        return this._client;
    }

    // -------------------------------------------------------------------------
    // Google OAuth
    // -------------------------------------------------------------------------

    async signInWithGoogle() {
        if (!this._client) throw new Error('Supabase client not initialised');

        const { data, error } = await this._client.auth.signInWithOAuth({
            provider: 'google',
            options: {
                redirectTo: `${window.location.origin}/auth/callback`,
            },
        });

        if (error) throw new Error(error.message || 'Failed to initiate Google sign-in');

        // signInWithOAuth performs a redirect — execution stops here on success.
        return data;
    }

    // -------------------------------------------------------------------------
    // Email + password sign-up
    // -------------------------------------------------------------------------

    async signUp(email, password, name = '') {
        if (!this._client) throw new Error('Supabase client not initialised');
        if (this._signupInProgress) throw new Error('Signup already in progress');

        this._signupInProgress = true;
        try {
            const { data, error } = await this._client.auth.signUp({
                email,
                password,
                options: {
                    data: { full_name: name },
                    emailRedirectTo: `${window.location.origin}/auth/callback`,
                },
            });

            if (error) throw new Error(error.message || 'Sign up failed');

            // When Supabase returns an immediate session (for example, when
            // email confirmation is disabled or auto-confirm is enabled),
            // establish the Flask session right away so the user lands in the app
            // without needing a second login step.
            if (data && data.session && data.session.access_token) {
                const callbackData = await this._postSessionToBackend(data.session, data.user);
                if (callbackData && callbackData.redirect) {
                    return { success: true, data, redirect: callbackData.redirect };
                }
            }

            return { success: true, data };
        } finally {
            this._signupInProgress = false;
        }
    }

    // -------------------------------------------------------------------------
    // Email OTP / token verification
    // -------------------------------------------------------------------------

    async verifyEmail(email, otp) {
        if (!this._client) throw new Error('Supabase client not initialised');

        const { data, error } = await this._client.auth.verifyOtp({
            email,
            token: otp,
            type: 'signup',
        });

        if (error) throw new Error(error.message || 'Verification failed');

        const access_token = data.session ? data.session.access_token : null;
        return { success: true, token: access_token, session: data.session };
    }

    async resendOtp(email, type = 'signup') {
        if (!this._client) throw new Error('Supabase client not initialised');

        const { error } = await this._client.auth.resend({ type, email });

        if (error) throw new Error(error.message || 'Failed to resend code');
        return { success: true };
    }

    // -------------------------------------------------------------------------
    // Email + password sign-in
    // -------------------------------------------------------------------------

    async signInWithEmailPassword(email, password) {
        if (!this._client) throw new Error('Supabase client not initialised');

        // Step 1: Sign in with Supabase
        const { data, error } = await this._client.auth.signInWithPassword({ email, password });

        if (!error) {
            const access_token = data.session ? data.session.access_token : null;
            if (!access_token) throw new Error('No access token returned from Supabase');

            // Step 2: Notify Flask backend to create/update the local session
            const callbackResponse = await fetch('/auth/supabase-callback', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    access_token,
                    user: data.user,
                }),
                credentials: 'include',
            });

            if (callbackResponse.ok) {
                const callbackData = await callbackResponse.json();
                return { success: true, redirect: callbackData.redirect || '/dashboard' };
            }

            throw new Error('Failed to establish Flask session');
        }

        // Local fallback: if Supabase credentials are rejected, allow the
        // app's bootstrap password to authenticate the portal user directly.
        const fallbackResponse = await fetch('/auth/customer-login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
            credentials: 'include',
        });

        if (fallbackResponse.ok) {
            const fallbackData = await fallbackResponse.json();
            return { success: true, redirect: fallbackData.redirect || '/dashboard', auth_source: 'local_password' };
        }

        throw new Error(error.message || 'Sign in failed');
    }

    // -------------------------------------------------------------------------
    // Session helpers
    // -------------------------------------------------------------------------

    async getSession() {
        if (!this._client) return null;
        const { data } = await this._client.auth.getSession();
        return data.session || null;
    }

    async getUser() {
        if (!this._client) return null;
        const { data } = await this._client.auth.getUser();
        return data.user || null;
    }

    async signOut() {
        if (!this._client) return { success: true };
        await this._client.auth.signOut();
        return { success: true };
    }

    // -------------------------------------------------------------------------
    // Browser callback helpers
    // -------------------------------------------------------------------------

    _isRecoveryOrInviteHash(hashParams) {
        const tokenType = hashParams.get('type');
        return tokenType === 'recovery' || tokenType === 'invite';
    }

    _isRecoveryOrInviteUrl(url) {
        const tokenType = (url.searchParams.get('type') || '').toLowerCase();
        const tokenHashType = (url.searchParams.get('token_type') || '').toLowerCase();
        const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
        const hashType = (hashParams.get('type') || '').toLowerCase();

        const recoveryTypes = new Set(['recovery', 'invite']);
        if (recoveryTypes.has(tokenType) || recoveryTypes.has(tokenHashType) || recoveryTypes.has(hashType)) {
            return true;
        }

        // PKCE recovery links often land with only ?code=... and rely on the
        // redirect target to decide which page should finish the flow.
        if (url.searchParams.has('code') && window.location.pathname !== '/auth/callback') {
            return true;
        }

        return false;
    }

    async _resolveSessionFromCurrentUrl() {
        if (!this._client) return null;

        const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/';
        const url = new URL(window.location.href);

        // Support modern Supabase PKCE redirects, which land as ?code=...
        const pkceCode = url.searchParams.get('code');
        if (pkceCode) {
            url.searchParams.delete('code');
            window.history.replaceState({}, '', `${url.pathname}${url.search ? url.search : ''}`);

            const { data, error } = await this._client.auth.exchangeCodeForSession(pkceCode);
            if (error) throw new Error(error.message || 'Failed to exchange auth code');
            if (data && data.session && data.session.access_token) {
                return data.session;
            }
        }

        // Support email-link verification flows that return token_hash / type
        // in the query string. Supabase can use either the newer token_hash
        // flow or the older confirmation URL redirect patterns, so we accept
        // both here.
        const tokenHash = url.searchParams.get('token_hash');
        const verifyType = url.searchParams.get('type');
        if (tokenHash && verifyType === 'email') {
            url.searchParams.delete('token_hash');
            url.searchParams.delete('type');
            window.history.replaceState({}, '', `${url.pathname}${url.search ? url.search : ''}`);

            const { data, error } = await this._client.auth.verifyOtp({
                token_hash: tokenHash,
                type: 'email',
            });
            if (error) throw new Error(error.message || 'Failed to verify email link');
            if (data && data.session && data.session.access_token) {
                return data.session;
            }
        }

        // Support implicit redirects, which land as #access_token=...
        // Never intercept recovery/invite links here; those are handled by the
        // dedicated reset-password page.
        if (normalizedPath !== '/auth/reset-password' && window.location.hash && window.location.hash.includes('access_token')) {
            const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
            if (this._isRecoveryOrInviteHash(hashParams)) {
                return null;
            }

            const accessToken = hashParams.get('access_token');
            const refreshToken = hashParams.get('refresh_token');

            if (accessToken && refreshToken) {
                window.history.replaceState({}, '', url.pathname);
                const { data, error } = await this._client.auth.setSession({
                    access_token: accessToken,
                    refresh_token: refreshToken,
                });
                if (error) throw new Error(error.message || 'Failed to set session from redirect');
                if (data && data.session && data.session.access_token) {
                    return data.session;
                }
            }
        }

        const currentSession = await this.getSession();
        if (currentSession && currentSession.access_token) {
            return currentSession;
        }

        return null;
    }

    async _postSessionToBackend(session, user) {
        if (!session || !session.access_token) return null;

        const response = await fetch('/auth/supabase-callback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                access_token: session.access_token,
                user,
            }),
            credentials: 'include',
        });

        if (!response.ok) return null;
        return response.json();
    }

    // -------------------------------------------------------------------------
    // OAuth redirect handler
    // Called on /auth/callback to complete browser redirects.
    // -------------------------------------------------------------------------

    async handleCallback() {
        if (!this._client || this._handlingCallback) return { success: false };

        this._handlingCallback = true;
        try {
            const session = await this._resolveSessionFromCurrentUrl();
            if (!session || !session.access_token) {
                return { success: false };
            }

            const user = await this.getUser();
            const callbackData = await this._postSessionToBackend(session, user);
            if (callbackData) {
                return { success: true, redirect: callbackData.redirect || '/dashboard' };
            }
        } catch (error) {
            console.error('handleCallback error:', error);
        } finally {
            this._handlingCallback = false;
        }

        return { success: false };
    }

    // -------------------------------------------------------------------------
    // Auto-handle auth redirects, but only when the current URL actually
    // contains Supabase auth parameters. This avoids firing background
    // callback requests on the normal sign-in / sign-up page.
    // -------------------------------------------------------------------------

    async checkSessionVerifier() {
        const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/';
        if (normalizedPath === '/auth/reset-password') return false;

        const url = new URL(window.location.href);
        if (normalizedPath !== '/auth/callback' && this._isRecoveryOrInviteUrl(url)) {
            const target = `/auth/reset-password${window.location.search}${window.location.hash}`;
            window.location.replace(target);
            return true;
        }

        const hasAuthParams =
            Boolean(window.location.search && /(\?|&)code=/.test(window.location.search)) ||
            Boolean(window.location.search && /(\?|&)token_hash=/.test(window.location.search)) ||
            Boolean(window.location.search && /(\?|&)error=/.test(window.location.search)) ||
            Boolean(window.location.hash && window.location.hash.includes('access_token'));

        if (normalizedPath !== '/auth/callback' && !hasAuthParams) {
            return false;
        }

        if (this._handlingCallback) return false;

        this._handlingCallback = true;
        try {
            const session = await this._resolveSessionFromCurrentUrl();
            if (!session || !session.access_token) return false;

            const user = await this.getUser();
            const callbackData = await this._postSessionToBackend(session, user);

            if (callbackData) {
                window.location.href = callbackData.redirect || '/dashboard';
                return true;
            }
        } catch (error) {
            console.error('checkSessionVerifier error:', error);
        } finally {
            this._handlingCallback = false;
        }

        return false;
    }
}

// Initialise client from meta tags injected by layout.html
document.addEventListener('DOMContentLoaded', function () {
    const urlMeta     = document.querySelector('meta[name="supabase-url"]');
    const anonKeyMeta = document.querySelector('meta[name="supabase-anon-key"]');

    const supabaseUrl     = urlMeta     ? urlMeta.content     : null;
    const supabaseAnonKey = anonKeyMeta ? anonKeyMeta.content : null;

    if (supabaseUrl && supabaseAnonKey) {
        window.supabaseAuth = new SupabaseAuthClient(supabaseUrl, supabaseAnonKey);

        // Legacy alias — any code that still calls window.neonAuth.* will work.
        window.neonAuth = window.supabaseAuth;

        window.supabaseAuth.checkSessionVerifier();
    }
});
