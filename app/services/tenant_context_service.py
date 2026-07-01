"""Tenant context service for request/session resolution.

This centralizes the platform's tenant hierarchy and active membership rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flask import g, request, session

from app.extensions import db
from app.domain import portal_for_role
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.user import User
from app.utils.roles import normalize_role, resolve_effective_role


@dataclass(frozen=True)
class TenantContext:
    user: Optional[User]
    tenant: Optional[Tenant]
    membership: Optional[TenantMembership]
    role: Optional[str]
    portal: str

    @property
    def tenant_id(self) -> Optional[int]:
        return getattr(self.tenant, 'tenant_id', None)

    @property
    def user_id(self) -> Optional[int]:
        return getattr(self.user, 'user_id', None)


class TenantContextService:
    """Resolve and manage active tenant context."""

    EXEMPT_PREFIXES = (
        '/auth/', '/static/', '/login', '/logout', '/register', '/favicon', '/about', '/help', '/billing/webhook',
    )

    EXEMPT_ENDPOINTS = {
        'main.index', 'main.login', 'main.login_post', 'main.logout', 'main.about', 'main.help_page', 'static'
    }

    def clear_context(self) -> None:
        g.current_tenant_id = None
        g.current_tenant = None
        g.current_membership = None
        g.current_tenant_context = None

    def get_current_user(self) -> Optional[User]:
        user = getattr(g, 'current_user', None)
        if user is not None:
            return user
        user_id = session.get('user_id')
        if not user_id:
            return None
        user = User.find_by_id(user_id)
        if user is not None:
            g.current_user = user
        return user

    def resolve_from_request(self) -> Optional[TenantContext]:
        """Resolve tenant context from URL path, session, or headers."""
        self.clear_context()
        path = request.path or ''
        endpoint = request.endpoint

        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return self._finalize_context()
        if endpoint in self.EXEMPT_ENDPOINTS:
            return self._finalize_context()

        tenant = None
        if path.startswith('/org/'):
            parts = [part for part in path.split('/') if part]
            if len(parts) >= 2:
                tenant = self._resolve_tenant_by_slug(parts[1])
        if tenant is None:
            tenant_id = session.get('current_tenant_id')
            if tenant_id:
                tenant = self._resolve_tenant_by_id(tenant_id)
        if tenant is None:
            header_tenant_id = request.headers.get('X-Tenant-ID')
            if header_tenant_id:
                try:
                    tenant = self._resolve_tenant_by_id(int(header_tenant_id))
                except (TypeError, ValueError):
                    tenant = None

        if tenant is not None:
            self.set_active_tenant(tenant)

        return self._finalize_context()

    def set_active_tenant(self, tenant: Tenant | int | None) -> Optional[Tenant]:
        if tenant is None:
            self.clear_context()
            return None
        if isinstance(tenant, int):
            tenant = self._resolve_tenant_by_id(tenant)
        if tenant is None:
            return None
        g.current_tenant_id = tenant.tenant_id
        g.current_tenant = tenant
        session['current_tenant_id'] = tenant.tenant_id
        membership = self._load_membership(tenant.tenant_id)
        g.current_membership = membership
        g.current_tenant_context = self._build_context(tenant, membership)
        return tenant

    def set_active_membership(self, membership: TenantMembership | None) -> None:
        g.current_membership = membership
        tenant = getattr(g, 'current_tenant', None)
        if tenant is not None:
            g.current_tenant_context = self._build_context(tenant, membership)

    def get_user_tenants(self, user_id: int) -> List[Dict[str, Any]]:
        rows = db.session.execute(
            db.select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == TenantMembership.STATUS_ACTIVE,
            ).order_by(TenantMembership.is_default.desc(), TenantMembership.id.asc())
        ).scalars().all()
        results: List[Dict[str, Any]] = []
        for membership in rows:
            tenant = membership.tenant
            if not tenant:
                tenant = self._resolve_tenant_by_id(membership.tenant_id)
            if tenant:
                results.append({
                    'tenant_id': tenant.tenant_id,
                    'name': tenant.name,
                    'slug': tenant.slug,
                    'role': membership.role,
                    'is_default': membership.is_default,
                    'status': tenant.status,
                })
        return results

    def get_context(self) -> TenantContext:
        tenant = getattr(g, 'current_tenant', None)
        membership = getattr(g, 'current_membership', None)
        user = self.get_current_user()
        role = resolve_effective_role(user=user, tenant_id=getattr(tenant, 'tenant_id', None), membership=membership)
        if role is None and membership is not None:
            role = normalize_role(getattr(membership, 'role', None))
        portal = portal_for_role(role)
        ctx = TenantContext(user=user, tenant=tenant, membership=membership, role=role, portal=portal)
        g.current_tenant_context = ctx
        return ctx

    def _finalize_context(self) -> Optional[TenantContext]:
        tenant = getattr(g, 'current_tenant', None)
        if tenant is not None:
            membership = self._load_membership(tenant.tenant_id)
            g.current_membership = membership
        return self.get_context()

    def _load_membership(self, tenant_id: int) -> Optional[TenantMembership]:
        user_id = session.get('user_id')
        if not user_id:
            return None
        membership = db.session.execute(
            db.select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == TenantMembership.STATUS_ACTIVE,
            )
        ).scalar_one_or_none()
        g.current_membership = membership
        return membership

    def _build_context(self, tenant: Tenant, membership: Optional[TenantMembership]) -> TenantContext:
        user = self.get_current_user()
        role = resolve_effective_role(user=user, tenant_id=tenant.tenant_id, membership=membership)
        portal = portal_for_role(role)
        return TenantContext(user=user, tenant=tenant, membership=membership, role=role, portal=portal)

    @staticmethod
    def _resolve_tenant_by_slug(slug: str) -> Optional[Tenant]:
        return Tenant.find_by_slug(slug)

    @staticmethod
    def _resolve_tenant_by_id(tenant_id: int) -> Optional[Tenant]:
        return Tenant.find_by_id(tenant_id)


_tenant_context_service = TenantContextService()
