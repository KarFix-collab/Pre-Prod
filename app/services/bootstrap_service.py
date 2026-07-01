"""Platform bootstrap helpers for KarFix."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import requests
from flask import Flask, current_app, url_for

from app.extensions import db
from app.models.user import User
from app.models.tenant_membership import TenantMembership

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    created: bool
    username: Optional[str]
    email: Optional[str]
    reason: Optional[str] = None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _bootstrap_email() -> str:
    return (os.environ.get('SUPERADMIN_EMAIL') or '').strip().lower()


def _bootstrap_password() -> str:
    return os.environ.get('SUPERADMIN_PASSWORD') or ''


def _bootstrap_username() -> str:
    return (os.environ.get('SUPERADMIN_USERNAME') or '').strip()


def _supabase_config() -> Tuple[str, str]:
    try:
        supabase_url = (current_app.config.get('SUPABASE_URL') or '').strip().rstrip('/')
        anon_key = (current_app.config.get('SUPABASE_ANON_KEY') or '').strip()
        return supabase_url, anon_key
    except Exception:
        supabase_url = (os.environ.get('SUPABASE_URL') or '').strip().rstrip('/')
        anon_key = (os.environ.get('SUPABASE_ANON_KEY') or '').strip()
        return supabase_url, anon_key


def _signup_redirect_url() -> str:
    try:
        configured = current_app.config.get('PORTAL_SIGNUP_REDIRECT_URL')
    except Exception:
        configured = None

    redirect_url = str(configured or '').strip()
    if not redirect_url:
        try:
            redirect_url = url_for('auth.callback', _external=True)
        except Exception:
            redirect_url = 'https://localhost:5000/auth/callback'

    redirect_url = str(redirect_url).strip()
    if redirect_url.startswith('http://'):
        redirect_url = 'https://' + redirect_url[7:]
    return redirect_url or 'https://localhost:5000/auth/callback'


def _unique_username(base_username: str) -> str:
    candidate = base_username.strip() or 'platform_owner'
    if not User.find_by_username(candidate):
        return candidate

    counter = 1
    while True:
        username = f"{candidate}{counter}"
        if not User.find_by_username(username):
            return username
        counter += 1


def _safe_to_promote(user: User) -> bool:
    """Only promote users that are not linked to tenants or customer records."""
    if getattr(user, 'customer_id', None):
        return False
    if getattr(user, 'preferred_tenant_id', None):
        return False
    try:
        memberships = db.session.execute(
            db.select(TenantMembership).where(TenantMembership.user_id == user.user_id)
        ).scalars().all()
        return len(memberships) == 0
    except Exception:
        logger.exception('Could not inspect tenant memberships for bootstrap user')
        return False


def _promote_local_user(user: User, *, email: str, username: str) -> BootstrapResult:
    if not _safe_to_promote(user):
        logger.warning(
            'SuperAdmin bootstrap refused for %s because the account is already linked to tenant/customer data',
            email,
        )
        return BootstrapResult(created=False, username=user.username, email=user.email, reason='existing_account_not_isolated')

    user.username = _unique_username(username)
    user.is_superadmin = True
    user.role = 'platform_owner'
    user.is_active = True
    user.email_verified = True
    user.preferred_tenant_id = None
    user.customer_id = None
    db.session.commit()
    logger.info('Promoted existing local account %s to SuperAdmin', email)
    return BootstrapResult(created=True, username=user.username, email=user.email, reason='promoted_existing_account')


def _bootstrap_supabase_auth_account(*, email: str, password: str, username: str) -> BootstrapResult:
    supabase_url, anon_key = _supabase_config()
    if not supabase_url or not anon_key:
        return BootstrapResult(created=False, username=username or None, email=email or None, reason='supabase_not_configured')

    payload = {
        'email': email,
        'password': password,
        'options': {
            'data': {
                'full_name': username,
                'role': 'platform_owner',
                'karfix_bootstrap': True,
            },
            'emailRedirectTo': _signup_redirect_url(),
        },
    }

    headers = {
        'apikey': anon_key,
        'Authorization': f'Bearer {anon_key}',
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(
            f"{supabase_url}/auth/v1/signup",
            json=payload,
            headers=headers,
            timeout=20,
        )
    except Exception as exc:
        logger.error('Failed to bootstrap Supabase Auth account for %s: %s', email, exc)
        return BootstrapResult(created=False, username=username or None, email=email or None, reason='supabase_signup_error')

    if response.ok:
        data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        user = data.get('user') or {}
        session = data.get('session') or {}
        user_id = user.get('id')
        reason = 'created_auth_account_pending_confirmation'
        if session.get('access_token'):
            reason = 'created_auth_account_with_session'
        if user_id:
            logger.info('Created Supabase Auth SuperAdmin bootstrap account for %s (id=%s)', email, user_id)
        return BootstrapResult(created=True, username=username or user.get('email') or email, email=user.get('email') or email, reason=reason)

    data = {}
    if response.headers.get('content-type', '').startswith('application/json'):
        try:
            data = response.json() or {}
        except Exception:
            data = {}

    error_text = str(data.get('error_description') or data.get('msg') or data.get('message') or '').lower()
    if response.status_code in {400, 422} and ('already' in error_text or 'registered' in error_text or 'exists' in error_text):
        logger.info('Supabase Auth SuperAdmin account already exists for %s', email)
        return BootstrapResult(created=False, username=username or None, email=email or None, reason='auth_account_already_exists')

    logger.error('Supabase signup failed for %s: %s', email, data or response.text)
    return BootstrapResult(created=False, username=username or None, email=email or None, reason='supabase_signup_failed')


def bootstrap_platform_superadmin(app: Optional[Flask] = None) -> Optional[BootstrapResult]:
    """Create the platform SuperAdmin in Supabase Auth and promote the local profile on first login.

    The bootstrap is controlled by environment variables:
      - SUPERADMIN_BOOTSTRAP_ENABLED (default: true outside tests)
      - SUPERADMIN_EMAIL
      - SUPERADMIN_PASSWORD
      - SUPERADMIN_USERNAME (optional)

    With the current deployment model, we bootstrap the *same* auth provider
    used by the login page (Supabase Auth) instead of creating a local-only
    password account. The local application profile is promoted later when the
    authenticated Supabase user first logs in.
    """

    def _run() -> Optional[BootstrapResult]:
        if app is not None and app.config.get('TESTING') and not _env_flag('SUPERADMIN_BOOTSTRAP_FORCE', False):
            return None

        enabled_default = not (app is not None and app.config.get('TESTING'))
        if not _env_flag('SUPERADMIN_BOOTSTRAP_ENABLED', enabled_default):
            return None

        email = _bootstrap_email()
        password = _bootstrap_password()
        username = _bootstrap_username()

        if not email or not password:
            logger.info('SuperAdmin bootstrap skipped: SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD must be set')
            return None

        base_username = username or email.split('@')[0] or 'platform_owner'

        # If a local profile already exists, only promote it when it is safe to do so.
        existing_local = User.find_by_email(email)
        promoted_local = None
        if existing_local:
            if existing_local.is_superadmin:
                existing_local.is_active = True
                existing_local.role = 'platform_owner'
                existing_local.preferred_tenant_id = None
                existing_local.customer_id = None
                db.session.commit()
                promoted_local = BootstrapResult(created=False, username=existing_local.username, email=existing_local.email, reason='updated_existing_superadmin_profile')
            else:
                promoted_local = _promote_local_user(existing_local, email=email, username=base_username)
                if promoted_local.reason == 'existing_account_not_isolated':
                    return promoted_local

        # Create or confirm the Supabase Auth identity used by the login page.
        auth_result = _bootstrap_supabase_auth_account(email=email, password=password, username=base_username)

        if promoted_local and promoted_local.created:
            # Keep the auth-provider result in the log but prioritize the local profile change.
            return promoted_local

        return auth_result

    if app is None:
        return _run()

    with app.app_context():
        return _run()
