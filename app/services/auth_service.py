"""
Authentication Service
Handles Supabase Auth JWT authentication
All authentication flows go through Supabase Auth
"""
from typing import Optional, Dict, Any, Tuple, List
import logging
import jwt
from jwt import PyJWKClient, PyJWKClientError
import requests
from functools import wraps
from flask import request, current_app, g, session, url_for
from app.models.user import User
from app.models.customer import Customer
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.extensions import db
from app.utils.roles import normalize_role, set_session_role, is_superadmin_session, resolve_effective_role, get_role_dashboard

logger = logging.getLogger(__name__)


class SupabaseAuthService:
    """Supabase Auth integration service for JWT authentication.

    Supabase now signs JWTs with the project's signing keys (JWKS), so the
    application verifies tokens against Supabase's JWKS endpoint instead of a
    shared HS256 secret.
    """

    def __init__(self, app=None):
        self.app = app
        self._jwks_client: Optional[PyJWKClient] = None
        self._jwks_url: Optional[str] = None

    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        self._jwks_client = None
        self._jwks_url = None

    @property
    def supabase_url(self) -> Optional[str]:
        """Get Supabase project URL from config"""
        return current_app.config.get('SUPABASE_URL')

    @property
    def jwks_url(self) -> Optional[str]:
        """Get the Supabase JWKS URL from config or derive it from the project URL."""
        configured = current_app.config.get('SUPABASE_JWKS_URL')
        if configured:
            return configured.rstrip('/')

        supabase_url = self.supabase_url
        if not supabase_url:
            return None

        return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    def _get_jwks_client(self) -> Optional[PyJWKClient]:
        """Return a cached PyJWKClient for the current JWKS URL."""
        jwks_url = self.jwks_url
        if not jwks_url:
            return None

        if self._jwks_client is None or self._jwks_url != jwks_url:
            self._jwks_client = PyJWKClient(jwks_url)
            self._jwks_url = jwks_url

        return self._jwks_client

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify a Supabase JWT token.

        Tries ES256 (JWKS) first — used by all new Supabase projects (2024+).
        Falls back to HS256 with SUPABASE_JWT_SECRET for older projects.
        """
        # --- ES256 via JWKS (new Supabase projects) ---
        jwks_client = self._get_jwks_client()
        if jwks_client:
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=['ES256'],
                    options={'verify_aud': False}
                )
                return payload
            except jwt.ExpiredSignatureError:
                logger.warning("Supabase token has expired")
                return None
            except PyJWKClientError as e:
                logger.debug(f"JWKS lookup failed, trying HS256 fallback: {e}")
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid Supabase token (ES256): {e}")
                return None
            except Exception as e:
                logger.debug(f"ES256 verification failed, trying HS256 fallback: {e}")

        # --- HS256 fallback (older Supabase projects using shared secret) ---
        jwt_secret = current_app.config.get('SUPABASE_JWT_SECRET')
        if jwt_secret:
            try:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=['HS256'],
                    options={'verify_aud': False}
                )
                return payload
            except jwt.ExpiredSignatureError:
                logger.warning("Supabase token has expired")
                return None
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid Supabase token (HS256): {e}")
                return None

        logger.error("No valid Supabase auth configuration (SUPABASE_URL or SUPABASE_JWT_SECRET required)")
        return None

    def get_user_from_token(self, token: str) -> Optional[User]:
        """Get or create a local User from a verified Supabase JWT"""
        payload = self.verify_token(token)
        if not payload:
            return None
        return User.authenticate_with_jwt(payload)

    def get_supabase_user(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetch user details from the Supabase Auth REST API.

        Used when you need fresh profile data (e.g. avatar, metadata)
        beyond what is encoded in the JWT payload.
        """
        supabase_url = self.supabase_url
        if not supabase_url:
            return None

        anon_key = current_app.config.get('SUPABASE_ANON_KEY', '')
        try:
            response = requests.get(
                f"{supabase_url.rstrip('/')}/auth/v1/user",
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'apikey': anon_key,
                },
                timeout=10
            )
            if response.ok:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Failed to fetch Supabase user: {e}")
            return None


# Global instance — kept as 'neon_auth' alias so that any code still
# importing 'neon_auth' from this module continues to work unchanged.
supabase_auth = SupabaseAuthService()
neon_auth = supabase_auth   # backwards-compatible alias


class AuthService:
    """Authentication service — all auth flows via Supabase Auth"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _get_user_memberships(self, user_id: int) -> List[Dict[str, Any]]:
        """Get active tenant memberships for a user"""
        try:
            rows = db.session.execute(
                db.select(TenantMembership).where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.status == TenantMembership.STATUS_ACTIVE,
                )
            ).scalars().all()

            return [
                {
                    'tenant_id': m.tenant_id,
                    'role': m.role,
                    'is_default': m.is_default,
                }
                for m in rows
            ]
        except Exception as e:
            self.logger.error(f"Failed to get memberships: {e}")
            return []


    def _link_customer_account(self, user: User, customer: Customer) -> None:
        """Persist an explicit portal-to-customer link for future logins."""
        try:
            changed = False
            if getattr(user, 'customer_id', None) != customer.customer_id:
                user.customer_id = customer.customer_id
                changed = True
            preferred_tenant_id = customer.preferred_tenant_id or customer.tenant_id
            if preferred_tenant_id and getattr(user, 'preferred_tenant_id', None) != preferred_tenant_id:
                user.preferred_tenant_id = preferred_tenant_id
                changed = True
            if changed:
                db.session.commit()
        except Exception as e:
            self.logger.debug(f"Failed to persist customer link for user {getattr(user, 'user_id', None)}: {e}")

    def _get_customer_for_user(self, user: Optional[User]) -> Optional[Customer]:
        """Find a portal customer record for an authenticated user."""
        try:
            if not user:
                return None

            # Prefer the explicit customer_id link (set by migration 008 / enable_portal_access).
            linked_customer_id = getattr(user, 'customer_id', None)
            if linked_customer_id:
                customer = db.session.get(Customer, linked_customer_id)
                if customer:
                    return customer

            # Fallback: match by email for accounts created before migration 008.
            # This keeps existing portal users working after the upgrade.
            if not user.email:
                return None

            email = user.email.strip()
            preferred_tenant_id = getattr(user, 'preferred_tenant_id', None)
            tenant_id = session.get('current_tenant_id')

            for tenant_choice in (preferred_tenant_id, tenant_id):
                if tenant_choice:
                    query = db.select(Customer).where(
                        Customer.email.ilike(email),
                        Customer.tenant_id == tenant_choice,
                    ).order_by(Customer.customer_id)
                    customer = db.session.execute(query).scalar_one_or_none()
                    if customer:
                        self._link_customer_account(user, customer)
                        return customer

            query = db.select(Customer).where(Customer.email.ilike(email)).order_by(Customer.customer_id)
            customer = db.session.execute(query).scalar_one_or_none()
            if customer:
                self._link_customer_account(user, customer)
            return customer

        except Exception as e:
            self.logger.error(f"Failed to get customer profile for user {getattr(user, 'user_id', None)}: {e}")
            return None

    def establish_customer_session(self, user: User, customer: Customer) -> None:
        """Establish session data for a customer portal login."""
        self._link_customer_account(user, customer)

        session['user_id'] = user.user_id
        session['username'] = user.username
        session['logged_in'] = True
        session['auth_method'] = 'supabase_auth'
        user.update_last_login()

        tenant = None
        preferred_tenant_id = customer.preferred_tenant_id or customer.tenant_id
        if preferred_tenant_id:
            tenant = Tenant.find_by_id(preferred_tenant_id)

        if tenant:
            session['current_tenant_id'] = tenant.tenant_id
            session['current_tenant_slug'] = tenant.slug
            session['current_tenant_name'] = tenant.name
            session['preferred_tenant_id'] = tenant.tenant_id

        set_session_role('customer')
        session['customer_id'] = customer.customer_id
        session['customer_name'] = customer.full_name

    def authenticate_jwt(self, token: str) -> Optional[User]:
        """Authenticate with Supabase JWT"""
        try:
            return supabase_auth.get_user_from_token(token)
        except Exception as e:
            self.logger.error(f"JWT authentication error: {e}")
            return None

    def get_current_user(self) -> Optional[User]:
        """Get current authenticated user from request context"""
        if hasattr(g, 'current_user') and g.current_user is not None:
            return g.current_user

        # Try JWT authentication (Authorization header)
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            user = self.authenticate_jwt(token)
            if user:
                g.current_user = user
                return user

        # Check for Supabase session token in cookie
        # Supabase JS client stores the access token in a cookie named
        # 'sb-<project-ref>-auth-token' (JSON-encoded).  We also accept
        # a plain Bearer token stored under the generic key used by the
        # Supabase JS v2 client.
        token = self._extract_supabase_cookie_token()
        if token:
            user = self.authenticate_jwt(token)
            if user:
                g.current_user = user
                return user

        # Fall back to Flask session-based authentication
        user_id = session.get('user_id')
        if user_id:
            user = User.find_by_id(user_id)
            if user and user.is_active:
                g.current_user = user
                return user

        g.current_user = None
        return None

    def _extract_supabase_cookie_token(self) -> Optional[str]:
        """Extract the Supabase access token from cookies.

        The Supabase JS v2 client stores auth state as JSON in a cookie
        named 'sb-<ref>-auth-token'.  We scan all cookies for one that
        starts with 'sb-' and try to parse the access_token field.
        Falls back to a raw cookie value if it looks like a JWT itself.
        """
        import json
        for name, value in request.cookies.items():
            if not name.startswith('sb-') or not name.endswith('-auth-token'):
                continue
            try:
                data = json.loads(value)
                token = data.get('access_token')
                if token:
                    return token
            except (json.JSONDecodeError, TypeError):
                # Cookie value might be a raw JWT string
                if value.startswith('eyJ'):
                    return value
        return None

    def establish_session(self, user: User) -> None:
        """Establish Flask session for an authenticated user (no tenant context yet)"""
        session['user_id'] = user.user_id
        session['username'] = user.username
        session['logged_in'] = True
        session['auth_method'] = 'supabase_auth'
        resolved_role = resolve_effective_role(user=user)
        if resolved_role:
            set_session_role(resolved_role)

    def establish_tenant_session(self, user_id: int, tenant_id: int) -> bool:
        """Set tenant context in session from TenantMembership"""
        try:
            from app.models.tenant import Tenant
            membership = db.session.execute(
                db.select(TenantMembership).where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.status == TenantMembership.STATUS_ACTIVE,
                )
            ).scalar_one_or_none()

            if not membership:
                return False

            tenant = Tenant.find_by_id(tenant_id)
            if not tenant:
                return False

            session['current_tenant_id'] = tenant_id
            session['current_tenant_slug'] = tenant.slug
            session['current_tenant_name'] = tenant.name
            set_session_role(membership.role)
            return True

        except Exception as e:
            self.logger.error(f"Failed to establish tenant session: {e}")
            return False

    def _has_pending_invitations(self, user_id: int) -> bool:
        """Check if user has any pending invitations"""
        try:
            count = db.session.execute(
                db.select(db.func.count()).select_from(TenantMembership).where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.status == TenantMembership.STATUS_PENDING,
                )
            ).scalar()
            return (count or 0) > 0
        except Exception as e:
            self.logger.error(f"Failed to check pending invitations: {e}")
            return False

    def resolve_post_auth_redirect(self, user_id: int) -> str:
        """Determine where to redirect after authentication based on memberships"""
        memberships = self._get_user_memberships(user_id)
        user = User.find_by_id(user_id)

        if user and getattr(user, 'is_superadmin', False):
            self.establish_session(user)
            return url_for('platform.home')

        if not memberships:
            # No active memberships — check for a linked customer portal account first.
            customer = self._get_customer_for_user(user)
            if customer:
                self.establish_customer_session(user, customer)
                return url_for('customer.dashboard')

            # Otherwise check for pending invitations.
            if self._has_pending_invitations(user_id):
                return url_for('auth.invitations')
            return url_for('auth.no_organization')

        if len(memberships) == 1:
            # Auto-select the single tenant
            self.establish_tenant_session(user_id, memberships[0]['tenant_id'])
            return url_for('main.dashboard')

        # Multiple memberships - try default first
        default = next((m for m in memberships if m['is_default']), None)
        if default:
            self.establish_tenant_session(user_id, default['tenant_id'])
            return url_for('main.dashboard')

        return url_for('auth.select_tenant')

    def switch_tenant(self, user_id: int, tenant_id: int) -> Tuple[bool, Optional[str]]:
        """Switch the active tenant for the current session."""
        success = self.establish_tenant_session(user_id, tenant_id)
        if success:
            return True, None
        return False, "You do not have access to this organization"

    def logout_user(self) -> None:
        """Clear user from session"""
        session.clear()
        if hasattr(g, 'current_user'):
            g.current_user = None


# Backwards-compatible legacy class name.
NeonAuthService = AuthService
