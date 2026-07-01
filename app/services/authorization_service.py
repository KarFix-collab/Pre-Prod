"""Central authorization service for KarFix.

The service evaluates access using the frozen architecture order:
identity -> membership -> role -> permission -> feature -> plan -> tenant overrides -> user overrides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flask import g

from app.domain import FEATURE_FLAGS, normalize_permission_name, permissions_for_role
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.user import User
from app.services.tenant_context_service import _tenant_context_service
from app.utils.roles import normalize_role, resolve_effective_role


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    permission: Optional[str] = None
    role: Optional[str] = None
    tenant_id: Optional[int] = None


class AuthorizationService:
    """Evaluate access for the current request context."""

    def context(self):
        ctx = getattr(g, 'current_tenant_context', None)
        if ctx is None:
            ctx = _tenant_context_service.get_context()
        return ctx

    def has_permission(
        self,
        permission: str,
        *,
        user: Optional[User] = None,
        tenant_id: Optional[int] = None,
        membership: Optional[TenantMembership] = None,
    ) -> bool:
        decision = self.authorize(permission, user=user, tenant_id=tenant_id, membership=membership)
        return decision.allowed

    def authorize(
        self,
        permission: str,
        *,
        user: Optional[User] = None,
        tenant_id: Optional[int] = None,
        membership: Optional[TenantMembership] = None,
        feature: Optional[str] = None,
    ) -> AuthorizationDecision:
        normalized_permission = normalize_permission_name(permission) or permission
        ctx = self.context()
        user = user or getattr(ctx, 'user', None)
        tenant = getattr(ctx, 'tenant', None)
        if tenant_id is not None and getattr(tenant, 'tenant_id', None) != tenant_id:
            tenant = Tenant.find_by_id(tenant_id)
        membership = membership or getattr(ctx, 'membership', None)

        if user is None:
            return AuthorizationDecision(False, 'anonymous', normalized_permission, tenant_id=tenant_id)
        if not getattr(user, 'is_active', True):
            return AuthorizationDecision(False, 'inactive_user', normalized_permission, tenant_id=tenant_id)
        if getattr(user, 'is_superadmin', False):
            return AuthorizationDecision(True, 'platform_owner', normalized_permission, role='platform_owner', tenant_id=getattr(tenant, 'tenant_id', tenant_id))

        effective_role = resolve_effective_role(user=user, tenant_id=getattr(tenant, 'tenant_id', tenant_id), membership=membership)
        if not effective_role:
            return AuthorizationDecision(False, 'missing_membership', normalized_permission, tenant_id=getattr(tenant, 'tenant_id', tenant_id))

        if feature and not self.feature_enabled(feature, tenant=tenant):
            return AuthorizationDecision(False, 'feature_disabled', normalized_permission, role=effective_role, tenant_id=getattr(tenant, 'tenant_id', tenant_id))

        role_permissions = permissions_for_role(effective_role)
        legacy_permission = self._legacy_permission_alias(normalized_permission)
        if normalized_permission in role_permissions or legacy_permission in role_permissions:
            return AuthorizationDecision(True, 'role_granted', normalized_permission, role=effective_role, tenant_id=getattr(tenant, 'tenant_id', tenant_id))

        # Tenant and user overrides are reserved for future patch work; the hooks
        # already exist here so the call signature remains stable.
        return AuthorizationDecision(False, 'permission_denied', normalized_permission, role=effective_role, tenant_id=getattr(tenant, 'tenant_id', tenant_id))

    def feature_enabled(self, feature: str, *, tenant: Optional[Tenant] = None) -> bool:
        feature = str(feature).strip().lower().replace(' ', '_')
        if not feature:
            return True
        if feature not in FEATURE_FLAGS:
            return True
        if tenant is None:
            tenant = getattr(self.context(), 'tenant', None)
        if tenant is None:
            return False
        settings = tenant.settings or {}
        feature_flags = settings.get('feature_flags') or settings.get('features') or {}
        if isinstance(feature_flags, dict):
            value = feature_flags.get(feature)
            if value is not None:
                return bool(value)
        enabled = settings.get('enabled_features')
        if isinstance(enabled, (list, tuple, set)):
            return feature in {str(item).strip().lower() for item in enabled}
        # Default to enabled for existing tenants during the transition.
        return True

    @staticmethod
    def _legacy_permission_alias(permission: str) -> Optional[str]:
        legacy = {
            'tenant.edit': 'manage_org',
            'identity.users.edit': 'manage_users',
            'workshop.parts.manage': 'manage_catalog',
            'workshop.inventory.manage': 'manage_inventory',
            'workshop.jobs.manage': 'manage_jobs',
            'crm.customers.manage': 'manage_customers',
            'billing.manage': 'manage_billing',
            'reports.view': 'view_reports',
        }
        return legacy.get(permission)


authorization_service = AuthorizationService()
