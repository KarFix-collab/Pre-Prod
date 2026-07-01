"""
Authentication Routes Blueprint
Handles Supabase Auth callbacks, email verification, session management,
and tenant selection.
"""
from flask import (
    Blueprint, request, redirect, url_for, jsonify,
    session, current_app, flash, render_template
)
import logging
from urllib.parse import urlparse
import requests as http_requests
from app.services.auth_service import AuthService, supabase_auth
from app.models.user import User
from app.extensions import db
from app.utils.security import PasswordSecurity
from app.utils.roles import set_session_role, normalize_role

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)


def _safe_attr(obj, attr, default=None):
    """Read an ORM scalar attribute without letting detached-instance errors escape."""
    try:
        return getattr(obj, attr)
    except Exception:
        return default


def _safe_user_snapshot(user: User) -> dict:
    """Return a JSON-serializable snapshot without triggering ORM refreshes."""
    if not user:
        return {}

    created_at = _safe_attr(user, 'created_at')
    last_login = _safe_attr(user, 'last_login')

    return {
        'user_id': _safe_attr(user, 'user_id'),
        'username': _safe_attr(user, 'username'),
        'email': _safe_attr(user, 'email'),
        'is_superadmin': _safe_attr(user, 'is_superadmin', False),
        'is_active': _safe_attr(user, 'is_active', True),
        'created_at': created_at.isoformat() if created_at else None,
        'last_login': last_login.isoformat() if last_login else None,
        'preferred_tenant_id': _safe_attr(user, 'preferred_tenant_id'),
        'customer_id': _safe_attr(user, 'customer_id'),
    }


def _default_reset_password_redirect() -> str:
    """Return the canonical password-reset redirect URL for this app."""
    try:
        configured = current_app.config.get('PORTAL_RESET_PASSWORD_URL')
    except Exception:
        configured = None

    redirect_url = str(configured or '').strip()
    if not redirect_url:
        try:
            redirect_url = url_for('auth.reset_password', _external=True)
        except Exception:
            redirect_url = 'https://localhost:5000/auth/reset-password'

    redirect_url = str(redirect_url).strip()
    if redirect_url.startswith('http://'):
        redirect_url = 'https://' + redirect_url[7:]
    return redirect_url or 'https://localhost:5000/auth/reset-password'


def _normalize_reset_password_redirect(redirect_to: str | None) -> str:
    """Force recovery links to land on /auth/reset-password.

    Supabase uses the supplied redirect URL verbatim when it is allowed.
    If an older client/template sends the site root or another path, we
    coerce it back to the canonical reset-password URL so recovery emails
    remain usable.
    """
    default_redirect = _default_reset_password_redirect()
    if not redirect_to:
        return default_redirect

    redirect_to = str(redirect_to).strip()
    if redirect_to.startswith('http://'):
        redirect_to = 'https://' + redirect_to[7:]

    try:
        parsed = urlparse(redirect_to)
        default_parsed = urlparse(default_redirect)
        if parsed.scheme not in ('https', ''):
            return default_redirect
        if parsed.netloc and parsed.netloc != default_parsed.netloc:
            return default_redirect
        if parsed.path.rstrip('/') != '/auth/reset-password':
            return default_redirect
        return redirect_to
    except Exception:
        return default_redirect


def _parse_local_password_hash(password_hash: str | None) -> tuple[str | None, str | None]:
    """Split a stored local password hash into its hash and salt parts."""
    if not password_hash:
        return None, None
    if ':' not in password_hash:
        return password_hash, None
    return password_hash.split(':', 1)


def _verify_local_password(user: User, password: str) -> bool:
    """Verify a local fallback password stored on the user row."""
    stored = getattr(user, 'password_hash', None)
    if not stored or not password:
        return False

    hash_part, salt_part = _parse_local_password_hash(stored)
    if not hash_part or not salt_part:
        return False
    return PasswordSecurity.verify_password(password, hash_part, salt_part)



# =============================================================================
# LOGIN PAGE
# =============================================================================

@auth_bp.route('/login')
def login():
    """Render the login/signup page"""
    if session.get('logged_in'):
        return redirect(url_for('main.dashboard'))
    return render_template('auth/login.html')


# =============================================================================
# SUPABASE AUTH CALLBACK ROUTES
# =============================================================================

@auth_bp.route('/callback')
def callback():
    """
    OAuth callback handler for Supabase Auth.
    Users are redirected here after OAuth sign-in (e.g. Google via Supabase).

    Supabase appends the session as a URL fragment (#access_token=...&refresh_token=...)
    which is not visible server-side.  We render the bridge page which reads
    the fragment client-side and POSTs the token to /auth/supabase-callback.
    """
    try:
        # Check for a token passed as a query param (some flows)
        access_token = request.args.get('access_token')

        if access_token:
            auth_service = AuthService()
            user = auth_service.authenticate_jwt(access_token)

            if user:
                auth_service.establish_session(user)
                redirect_url = auth_service.resolve_post_auth_redirect(_safe_attr(user, 'user_id'))
                return redirect(redirect_url)

        # No server-side token — render bridge page that reads the URL
        # fragment client-side and forwards to Flask via supabase-callback.
        logger.info("OAuth callback without server-side token, rendering bridge page")
        return render_template('auth/oauth_completing.html')

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))


@auth_bp.route('/customer-login', methods=['POST'])
def customer_login():
    """Local password fallback for customer portal and legacy users."""
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        user = User.find_by_email(email)
        if not user or not user.is_active or not _verify_local_password(user, password):
            return jsonify({'error': 'Invalid login credentials'}), 401

        auth_service = AuthService()
        auth_service.establish_session(user)
        redirect_url = auth_service.resolve_post_auth_redirect(_safe_attr(user, 'user_id'))

        return jsonify({
            'success': True,
            'redirect': redirect_url,
            'auth_source': 'local_password',
        })
    except Exception as e:
        logger.error(f"Customer local login error: {e}")
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/sync-local-password', methods=['POST'])
def sync_local_password():
    """Keep the local fallback password aligned with the Supabase password."""
    try:
        data = request.get_json(silent=True) or {}
        access_token = data.get('access_token')
        password = data.get('password') or ''

        if not access_token or not password:
            return jsonify({'error': 'Access token and password are required'}), 400

        auth_service = AuthService()
        user = auth_service.authenticate_jwt(access_token)
        if not user or not getattr(user, 'email', None):
            return jsonify({'error': 'Invalid session'}), 401

        local_user = User.find_by_email(user.email.strip().lower()) or user
        if not local_user or not getattr(local_user, 'is_active', False):
            return jsonify({'error': 'User not found'}), 404

        local_hash, local_salt = PasswordSecurity.hash_password(password)
        local_user.password_hash = f"{local_hash}:{local_salt}"
        local_user.email_verified = True
        db.session.commit()

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Sync local password error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/supabase-callback', methods=['GET', 'POST'])
def supabase_callback():
    """
    API endpoint for the JavaScript client to notify the backend of a
    successful Supabase Auth sign-in or sign-up.

    Accepts the Supabase access_token (JWT) from the request body.
    The client-side supabase-auth.js calls this after every successful auth.
    """
    try:
        if request.method == 'GET':
            # Keep browser navigations on the canonical callback page. The
            # frontend bridge handles Supabase query fragments/code exchange
            # and then POSTs back to this endpoint.
            return redirect(url_for('auth.callback'))

        body = request.get_json(silent=True) or {}
        access_token = body.get('access_token') or body.get('token')

        # Also accept token passed directly in the Authorization header
        if not access_token:
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                access_token = auth_header[7:]

        if not access_token:
            return jsonify({'error': 'No access token provided'}), 401

        auth_service = AuthService()
        try:
            db.session.rollback()
        except Exception:
            pass
        user = auth_service.authenticate_jwt(access_token)

        if not user:
            # Fallback: client may have passed decoded user data
            client_user = body.get('user')
            if client_user and client_user.get('id') and client_user.get('email'):
                try:
                    db.session.rollback()
                except Exception:
                    pass
                fallback_payload = {
                    'sub': client_user['id'],
                    'email': client_user['email'],
                    'name': client_user.get('name', ''),
                    'email_verified': client_user.get('email_confirmed_at') is not None,
                }
                try:
                    user = User.authenticate_with_jwt(fallback_payload)
                except Exception as fallback_error:
                    logger.error(f"Fallback Supabase user bootstrap failed: {fallback_error}")
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    user = None

                    # Last-resort recovery: attach the Supabase identity to an
                    # existing local user row if one was already provisioned.
                    try:
                        existing = User.find_by_email(client_user['email'])
                        if existing and existing.is_active:
                            existing.supabase_user_id = str(client_user['id'])
                            existing.email_verified = True
                            db.session.commit()
                            user = existing
                    except Exception as lookup_error:
                        logger.error(f"Supabase fallback lookup failed: {lookup_error}")
                        try:
                            db.session.rollback()
                        except Exception:
                            pass

        if user:
            try:
                auth_service.establish_session(user)
            except Exception as session_error:
                logger.error(f"Failed to establish Flask session: {session_error}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                # Keep the browser session alive even if the last-login update
                # or a transient DB issue fails.
                session['user_id'] = _safe_attr(user, 'user_id')
                session['username'] = _safe_attr(user, 'username')
                session['logged_in'] = True
                session['auth_method'] = 'supabase_auth'
                if _safe_attr(user, 'is_superadmin', False):
                    set_session_role('platform_owner')

            try:
                redirect_url = auth_service.resolve_post_auth_redirect(_safe_attr(user, 'user_id'))
            except Exception as redirect_error:
                logger.error(f"Post-auth redirect resolution failed: {redirect_error}")
                try:
                    redirect_url = url_for('auth.no_organization')
                except Exception:
                    redirect_url = url_for('main.dashboard')

            user_snapshot = _safe_user_snapshot(user)
            return jsonify({
                'success': True,
                'user': user_snapshot,
                'redirect': redirect_url,
            })

        logger.warning("supabase-callback: authentication failed")
        return jsonify({'error': 'Invalid session'}), 401

    except Exception as e:
        logger.error(f"Supabase callback error: {e}")
        return jsonify({'error': str(e)}), 500


# Legacy route alias so any bookmarked or cached POST to /auth/neon-callback
# still works during the transition period.
@auth_bp.route('/neon-callback', methods=['POST'])
def neon_callback():
    """Legacy alias for /auth/supabase-callback — kept for transition."""
    return supabase_callback()


# =============================================================================
# EMAIL VERIFICATION
# =============================================================================

@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    """
    Proxy email OTP verification to Supabase Auth.

    Supabase uses token-based email confirmation rather than an OTP endpoint
    when using the default email provider, but this proxy supports setups that
    use the PKCE / OTP flow via the Supabase Auth REST API.
    """
    try:
        data = request.get_json()
        email = data.get('email')
        otp = data.get('otp') or data.get('token')

        if not email or not otp:
            return jsonify({'error': 'Email and verification code are required'}), 400

        supabase_url = current_app.config.get('SUPABASE_URL', '').rstrip('/')
        anon_key = current_app.config.get('SUPABASE_ANON_KEY', '')
        if not supabase_url:
            return jsonify({'error': 'Auth service not configured'}), 500

        response = http_requests.post(
            f"{supabase_url}/auth/v1/verify",
            json={'email': email, 'token': otp, 'type': 'signup'},
            headers={'apikey': anon_key, 'Content-Type': 'application/json'},
            timeout=10
        )

        if response.ok:
            return jsonify({'success': True})

        error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        return jsonify({
            'error': error_data.get('msg', error_data.get('message', 'Verification failed'))
        }), response.status_code

    except Exception as e:
        logger.error(f"Email verification error: {e}")
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/verify-email')
def verify_email_page():
    """Standalone email verification page for users who navigated away"""
    return render_template('auth/verify_email.html')


# =============================================================================
# FORGOT PASSWORD PROXY
# =============================================================================

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Proxy forgot-password requests to Supabase Auth"""
    try:
        data = request.get_json(silent=True) or {}
        email = data.get('email')

        if not email:
            return jsonify({'error': 'Email is required'}), 400

        supabase_url = current_app.config.get('SUPABASE_URL', '').rstrip('/')
        anon_key = current_app.config.get('SUPABASE_ANON_KEY', '')
        if not supabase_url:
            return jsonify({'error': 'Auth service not configured'}), 500

        redirect_to = _normalize_reset_password_redirect(data.get('redirect_to'))

        logger.info('Password recovery redirect_to=%s email=%s', redirect_to, email)

        try:
            response = http_requests.post(
                f"{supabase_url}/auth/v1/recover",
                json={'email': email, 'redirect_to': redirect_to},
                headers={'apikey': anon_key, 'Content-Type': 'application/json'},
                timeout=30,
            )
        except http_requests.Timeout:
            logger.warning(
                'Forgot password request timed out after the email may already have been accepted. email=%s redirect_to=%s',
                email,
                redirect_to,
            )
            return jsonify({
                'success': True,
                'message': 'If an account exists for this email, a reset link may arrive shortly.',
                'warning': 'Password recovery request timed out while waiting for Supabase.',
            }), 200

        if response.ok:
            return jsonify({'success': True})

        error_data = {}
        if response.headers.get('content-type', '').startswith('application/json'):
            error_data = response.json()
        return jsonify({
            'error': error_data.get('msg', error_data.get('message', 'Could not send reset email'))
        }), response.status_code

    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# SESSION & API ROUTES
# =============================================================================

@auth_bp.route('/session')
def get_session():
    """Get current session info for the frontend"""
    if session.get('logged_in'):
        return jsonify({
            'authenticated': True,
            'user_id': session.get('user_id'),
            'username': session.get('username'),
            'role': session.get('current_role'),
            'tenant_id': session.get('current_tenant_id'),
            'auth_method': session.get('auth_method', 'supabase_auth')
        })

    return jsonify({'authenticated': False})


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """API endpoint for logout"""
    try:
        session.clear()

        response_data = {
            'success': True,
            'message': 'Logged out successfully'
        }

        # Provide the Supabase sign-out URL so the JS client can call
        # supabase.auth.signOut() to clear its local session storage.
        supabase_url = current_app.config.get('SUPABASE_URL', '')
        if supabase_url:
            response_data['supabase_signout_url'] = f"{supabase_url.rstrip('/')}/auth/v1/logout"

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Logout error: {e}")
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/verify-token', methods=['POST'])
def verify_token():
    """Verify a Supabase JWT token"""
    try:
        data = request.get_json()
        token = data.get('token') or data.get('access_token')

        if not token:
            return jsonify({'valid': False, 'error': 'No token provided'}), 400

        payload = supabase_auth.verify_token(token)

        if payload:
            return jsonify({
                'valid': True,
                'payload': {
                    'sub': payload.get('sub'),
                    'email': payload.get('email'),
                    'name': payload.get('name') or payload.get('user_metadata', {}).get('full_name'),
                }
            })

        return jsonify({'valid': False, 'error': 'Invalid token'}), 401

    except Exception as e:
        logger.error(f"Token verification error: {e}")
        return jsonify({'valid': False, 'error': str(e)}), 500


@auth_bp.route('/link-account', methods=['POST'])
def link_account():
    """Link a Supabase Auth account to an existing local user account"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()
        # Accept both legacy and current key names for auth user IDs.
        external_auth_user_id = (
            data.get('supabase_user_id')
            or data.get('neon_auth_user_id')
            or data.get('external_auth_user_id')
        )

        if not external_auth_user_id:
            return jsonify({'error': 'No auth user ID provided'}), 400

        user_id = session.get('user_id')
        user = User.find_by_id(user_id)

        if not user:
            return jsonify({'error': 'User not found'}), 404

        existing = User.find_by_neon_auth_id(external_auth_user_id)
        if existing and existing.user_id != user.user_id:
            return jsonify({'error': 'This account is already linked to another user'}), 400

        user.supabase_user_id = external_auth_user_id
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Account linked successfully'
        })

    except Exception as e:
        logger.error(f"Account linking error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/status')
def auth_status():
    """Check auth provider configuration status"""
    configured = bool(current_app.config.get('SUPABASE_URL') and current_app.config.get('SUPABASE_ANON_KEY'))
    return jsonify({
        'supabase_auth_configured': configured,
        # Legacy key kept for any clients that check this field
        'neon_auth_configured': configured,
    })


# =============================================================================
# NO ORGANIZATION PAGE
# =============================================================================

@auth_bp.route('/no-organization')
def no_organization():
    """Page shown when authenticated user has no tenant memberships"""
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    from app.services.tenant_service import TenantService
    tenant_service = TenantService()
    user_id = session.get('user_id')
    pending = tenant_service.get_pending_invitations(user_id)
    if pending:
        return redirect(url_for('auth.invitations'))

    return render_template('auth/no_organization.html')


@auth_bp.route('/invitations')
def invitations():
    """Show pending invitations for the current user"""
    if not session.get('logged_in'):
        flash('Please log in first', 'warning')
        return redirect(url_for('auth.login'))

    from app.services.tenant_service import TenantService
    tenant_service = TenantService()
    user_id = session.get('user_id')

    pending = tenant_service.get_pending_invitations(user_id)
    active_tenants = tenant_service.get_user_tenants(user_id)

    return render_template(
        'auth/invitations.html',
        invitations=pending,
        has_active_tenants=len(active_tenants) > 0,
    )


@auth_bp.route('/invitations/<int:membership_id>/accept', methods=['POST'])
def accept_invitation(membership_id):
    """Accept a pending invitation"""
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    from app.services.tenant_service import TenantService
    from app.services.auth_service import AuthService

    tenant_service = TenantService()
    auth_service = AuthService()
    user_id = session.get('user_id')

    success, errors = tenant_service.accept_invitation(membership_id, user_id)

    if success:
        from app.models.tenant_membership import TenantMembership
        membership = TenantMembership.find_by_id(membership_id)
        if membership:
            auth_service.establish_tenant_session(user_id, membership.tenant_id)
        flash('Invitation accepted! Welcome to the team.', 'success')
        return redirect(url_for('main.dashboard'))
    else:
        for error in errors:
            flash(error, 'error')
        return redirect(url_for('auth.invitations'))


@auth_bp.route('/invitations/<int:membership_id>/decline', methods=['POST'])
def decline_invitation(membership_id):
    """Decline a pending invitation"""
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    from app.services.tenant_service import TenantService

    tenant_service = TenantService()
    user_id = session.get('user_id')

    success, errors = tenant_service.decline_invitation(membership_id, user_id)

    if success:
        flash('Invitation declined.', 'info')
    else:
        for error in errors:
            flash(error, 'error')

    return redirect(url_for('auth.invitations'))


# =============================================================================
# MULTI-TENANT ROUTES
# =============================================================================

@auth_bp.route('/select-tenant')
def select_tenant():
    """Tenant selection page - shown when user has multiple organizations"""
    if not session.get('logged_in'):
        flash('Please log in first', 'warning')
        return redirect(url_for('auth.login'))

    from app.services.tenant_service import TenantService
    tenant_service = TenantService()
    user_id = session.get('user_id')
    tenants = tenant_service.get_user_tenants(user_id)

    if not tenants:
        return redirect(url_for('auth.no_organization'))

    if len(tenants) == 1:
        tenant = tenants[0]
        session['current_tenant_id'] = tenant['tenant_id']
        session['current_tenant_slug'] = tenant['slug']
        session['current_tenant_name'] = tenant['name']
        set_session_role(tenant['role'])
        return redirect(url_for('main.dashboard'))

    return render_template('auth/select_tenant.html', tenants=tenants)


@auth_bp.route('/switch-tenant', methods=['POST'])
def switch_tenant():
    """Switch active tenant context"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Not authenticated'}), 401

    tenant_slug = request.form.get('tenant_slug') or (request.get_json(silent=True) or {}).get('tenant_slug')
    if not tenant_slug:
        flash('No organization specified', 'error')
        return redirect(url_for('auth.select_tenant'))

    from app.services.tenant_service import TenantService
    from app.models.tenant import Tenant

    tenant = Tenant.find_by_slug(tenant_slug)
    if not tenant:
        flash('Organization not found', 'error')
        return redirect(url_for('auth.select_tenant'))

    tenant_service = TenantService()
    user_tenants = tenant_service.get_user_tenants(session.get('user_id'))
    tenant_data = next((t for t in user_tenants if t['slug'] == tenant_slug), None)

    if not tenant_data:
        flash('You are not a member of this organization', 'error')
        return redirect(url_for('auth.select_tenant'))

    session['current_tenant_id'] = tenant_data['tenant_id']
    session['current_tenant_slug'] = tenant_data['slug']
    session['current_tenant_name'] = tenant_data['name']
    set_session_role(tenant_data['role'])

    flash(f'Switched to {tenant_data["name"]}', 'success')
    return redirect(url_for('main.dashboard'))


@auth_bp.route('/register-organization', methods=['GET', 'POST'])
def register_organization():
    """Register a new organization (tenant)"""
    if not session.get('logged_in'):
        flash('Please log in first', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('auth/register_organization.html')

    from app.services.tenant_service import TenantService
    from app.utils.validators import sanitize_input

    name = sanitize_input(request.form.get('name', ''))
    business_type = sanitize_input(request.form.get('business_type', 'auto_repair'))
    email = sanitize_input(request.form.get('email', ''))
    phone = sanitize_input(request.form.get('phone', ''))
    address = sanitize_input(request.form.get('address', ''))

    if not name or len(name) < 2:
        flash('Organization name must be at least 2 characters', 'error')
        return render_template('auth/register_organization.html')

    tenant_service = TenantService()
    user_id = session.get('user_id')

    success, errors, tenant = tenant_service.create_tenant(
        name=name,
        owner_user_id=user_id,
        business_type=business_type,
        email=email or None,
        phone=phone or None,
        address=address or None,
    )

    if success:
        session['current_tenant_id'] = tenant.tenant_id
        session['current_tenant_slug'] = tenant.slug
        session['current_tenant_name'] = tenant.name
        set_session_role('owner')

        flash(f'Organization "{tenant.name}" created successfully!', 'success')
        return redirect(url_for('onboarding.step', step_number=1))
    else:
        for error in errors:
            flash(error, 'error')
        return render_template('auth/register_organization.html')


# =============================================================================
# PASSWORD RESET PAGE
# =============================================================================

@auth_bp.route('/reset-password')
def reset_password():
    """Password reset page — Supabase redirects here after reset email link click.
    The access token arrives in the URL fragment (#access_token=...) which is
    handled client-side by supabase-auth.js / the Supabase JS client.
    """
    return render_template('auth/reset_password.html')
