"""
Role and permission helpers for KarFix.

This module centralizes role normalization so the app treats legacy values
and the current RBAC model consistently across views, decorators, and tests.
"""
from __future__ import annotations

from typing import Iterable, Optional

from flask import session

from app.domain import ROLE_PERMISSION_MAP, normalize_role_name, normalize_permission_name, portal_for_role

SUPERADMIN_ROLE = 'platform_owner'
TENANT_ADMIN_ROLE = 'tenant_admin'
CUSTOMER_ROLE = 'customer'
LEGACY_TENANT_ADMIN_ROLES = {'owner', 'admin', 'manager'}
PLATFORM_ADMIN_ROLES = {SUPERADMIN_ROLE, TENANT_ADMIN_ROLE, 'owner', 'admin', 'superadmin', 'platform_owner'}
STAFF_PORTAL_ROLES = {TENANT_ADMIN_ROLE, 'owner', 'admin', 'manager', 'technician', SUPERADMIN_ROLE}


def normalize_role(role: Optional[str]) -> Optional[str]:
    """Return a canonical role name or None."""
    if role is None:
        return None
    return normalize_role_name(role)


def canonical_portal_role(role: Optional[str] = None, *, user=None) -> Optional[str]:
    """Collapse legacy roles into the current portal-facing role system.

    The current platform only distinguishes three portal scopes at the top
    level: superadmin, tenant_admin and customer. Legacy workshop roles
    are normalized to tenant_admin for platform navigation and dashboard
    routing while still remaining available in tenant-specific membership
    records for Phase 2B.
    """
    if user is not None and getattr(user, 'is_superadmin', False):
        return SUPERADMIN_ROLE

    normalized = normalize_role(role)
    if normalized in (SUPERADMIN_ROLE, CUSTOMER_ROLE, TENANT_ADMIN_ROLE):
        return normalized
    if normalized in LEGACY_TENANT_ADMIN_ROLES:
        return TENANT_ADMIN_ROLE
    if normalized == 'technician':
        return TENANT_ADMIN_ROLE
    if normalized == 'parts_clerk':
        return TENANT_ADMIN_ROLE
    if normalized == 'viewer':
        return CUSTOMER_ROLE
    if normalized:
        return normalized
    if user is not None:
        legacy_role = normalize_role(getattr(user, 'role', None))
        if legacy_role in LEGACY_TENANT_ADMIN_ROLES:
            return TENANT_ADMIN_ROLE
        if legacy_role:
            return legacy_role
    return current_role_name()


def current_role_name(default: Optional[str] = None) -> Optional[str]:
    """Return the normalized session role."""
    return normalize_role(session.get('current_role', default))


def set_session_role(role: Optional[str]) -> Optional[str]:
    """Persist a normalized role into the Flask session."""
    normalized = normalize_role(role)
    if normalized:
        session['current_role'] = normalized
    return normalized


def is_superadmin_session() -> bool:
    return current_role_name() == SUPERADMIN_ROLE


def is_tenant_admin_session() -> bool:
    return current_role_name() == TENANT_ADMIN_ROLE


def is_platform_admin_session() -> bool:
    return current_role_name() in PLATFORM_ADMIN_ROLES or is_superadmin_session()


def is_staff_session() -> bool:
    return current_role_name() in STAFF_PORTAL_ROLES or is_tenant_admin_session()


def role_has_permission(role: Optional[str], permission: str) -> bool:
    """Check whether a normalized role grants a permission."""
    normalized = normalize_role(role)
    if normalized == SUPERADMIN_ROLE or normalized == 'superadmin':
        return True
    permission = normalize_permission_name(permission) or permission
    if normalized == TENANT_ADMIN_ROLE:
        return permission in ROLE_PERMISSION_MAP.get('tenant_admin', []) or permission in {'manage_users', 'manage_jobs', 'manage_customers', 'manage_billing', 'view_reports', 'manage_catalog', 'manage_inventory', 'manage_org'}
    role_permissions = set(ROLE_PERMISSION_MAP.get(normalized or '', []))
    if permission in role_permissions:
        return True
    # Backward-compatible alias matching for legacy permission names.
    legacy_aliases = {
        'tenant.edit': 'manage_org',
        'identity.users.edit': 'manage_users',
        'workshop.parts.manage': 'manage_catalog',
        'workshop.inventory.manage': 'manage_inventory',
        'workshop.jobs.manage': 'manage_jobs',
        'crm.customers.manage': 'manage_customers',
        'billing.manage': 'manage_billing',
        'reports.view': 'view_reports',
    }
    legacy = legacy_aliases.get(permission)
    return legacy in role_permissions if legacy else False


def normalize_role_list(roles: Iterable[str]) -> set[str]:
    return {normalized for role in roles if (normalized := normalize_role(role))}


def can_access_admin_portal(role: Optional[str] = None) -> bool:
    """Return True when a role may access tenant admin pages."""
    normalized = normalize_role(role) or current_role_name()
    return normalized in PLATFORM_ADMIN_ROLES


def can_access_superadmin_portal(role: Optional[str] = None, *, user=None) -> bool:
    """Return True when a role may access platform-wide admin pages."""
    if user is not None and getattr(user, 'is_superadmin', False):
        return True
    normalized = normalize_role(role) or current_role_name()
    return normalized == SUPERADMIN_ROLE


def can_access_staff_portal(role: Optional[str] = None) -> bool:
    """Return True when a role may access technician/staff pages."""
    normalized = normalize_role(role) or current_role_name()
    return normalized in STAFF_PORTAL_ROLES


def resolve_effective_role(*, user=None, tenant_id: Optional[int] = None, membership=None, default: Optional[str] = None) -> Optional[str]:
    """Resolve the canonical role for the current user context."""
    if user is not None and getattr(user, 'is_superadmin', False):
        return SUPERADMIN_ROLE

    if membership is not None:
        membership_role = normalize_role(getattr(membership, 'role', None))
        if membership_role:
            if membership_role in LEGACY_TENANT_ADMIN_ROLES:
                return TENANT_ADMIN_ROLE
            return membership_role

    if user is not None and tenant_id is not None and hasattr(user, 'get_role_in_tenant'):
        tenant_role = normalize_role(user.get_role_in_tenant(tenant_id))
        if tenant_role:
            if tenant_role in LEGACY_TENANT_ADMIN_ROLES:
                return TENANT_ADMIN_ROLE
            return tenant_role

    if user is not None:
        legacy_role = normalize_role(getattr(user, 'role', None))
        if legacy_role:
            if legacy_role in LEGACY_TENANT_ADMIN_ROLES:
                return TENANT_ADMIN_ROLE
            return legacy_role

    return normalize_role(default) or current_role_name(default)


def get_role_dashboard(role: Optional[str]) -> str:
    """Return the default dashboard route name for a role."""
    normalized = normalize_role(role)
    if normalized == CUSTOMER_ROLE:
        return 'customer.dashboard'
    if normalized == SUPERADMIN_ROLE:
        return 'platform.home'
    if normalized == TENANT_ADMIN_ROLE or normalized in ('owner', 'admin', 'manager'):
        return 'main.dashboard'
    return 'main.dashboard'
