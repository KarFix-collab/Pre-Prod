"""KarFix canonical domain, portal, and permission registry.

This module centralizes the architectural vocabulary used by the platform.
It intentionally keeps backward-compatible aliases for the legacy role and
permission names that already exist in the current application so the refactor
can happen incrementally.
"""
from __future__ import annotations

from typing import Dict, List, Set

# High-level platform domains used by architecture, portal routing, and tracing.
CANONICAL_DOMAINS: List[str] = [
    'identity_access',
    'tenant_management',
    'crm',
    'vehicle_management',
    'workshop_operations',
    'fleet_operations',
    'enterprise_management',
    'inventory_procurement',
    'finance_billing',
    'scheduling',
    'analytics_reporting',
    'communications',
    'documents',
    'integrations',
    'platform_administration',
    'audit_compliance',
]

PORTALS: List[str] = [
    'platform_owner',
    'franchise',
    'workshop',
    'enterprise_fleet',
    'fleet',
    'customer',
]

PORTAL_MODULES: Dict[str, List[str]] = {
    'platform_owner': [
        'Executive Dashboard', 'Tenant Management', 'Franchise Management',
        'Workshop Management', 'Enterprise Fleet Management', 'Subscription & Billing',
        'Identity & Security', 'Platform Analytics', 'Financial Operations',
        'Branding', 'Integrations', 'Support Centre', 'Platform Configuration',
    ],
    'franchise': [
        'Dashboard', 'Workshop Management', 'Staff', 'Branding', 'Finance',
        'Marketing', 'Fleet Customers', 'Reports', 'Support', 'Franchise Settings',
    ],
    'workshop': [
        'Dashboard', 'Customers', 'Vehicles', 'Bookings', 'Job Cards',
        'Digital Inspections', 'Estimates', 'Approvals', 'Work Orders',
        'Technician Board', 'Workshop Calendar', 'Parts', 'Suppliers',
        'Inventory', 'Purchasing', 'Invoicing', 'Payments', 'Warranty',
        'Service History', 'CRM', 'Reports', 'Analytics', 'Employees',
        'Payroll Export', 'Settings',
    ],
    'enterprise_fleet': [
        'Dashboard', 'Vehicles', 'Drivers', 'Workshops', 'Jobs', 'Authorisations',
        'Budgets', 'Contracts', 'Reports', 'Analytics', 'Billing',
    ],
    'fleet': ['Dashboard', 'Vehicles', 'Drivers', 'Jobs', 'Approvals', 'Reports', 'Billing', 'Settings'],
    'customer': ['Dashboard', 'My Vehicles', 'Service History', 'Book Service', 'Quotes', 'Approvals', 'Invoices', 'Payments', 'Documents', 'Messages', 'Notifications', 'Profile'],
}

USER_TYPES: List[str] = [
    'platform_user',
    'franchise_user',
    'workshop_user',
    'fleet_user',
    'customer_user',
    'partner_supplier_user',
    'system_user',
]

ACCESS_LEVELS: List[str] = ['owner', 'admin', 'operator', 'viewer']

# Canonical permissions. The application can continue to use legacy permission
# names during the transition because PERMISSION_ALIASES maps them into this set.
PERMISSION_CATALOG: List[str] = [
    'identity.users.view',
    'identity.users.invite',
    'identity.users.edit',
    'identity.users.suspend',
    'identity.memberships.manage',
    'tenant.view',
    'tenant.create',
    'tenant.edit',
    'tenant.transfer',
    'tenant.migrate',
    'tenant.suspend',
    'tenant.archive',
    'crm.customers.view',
    'crm.customers.manage',
    'vehicle.view',
    'vehicle.manage',
    'workshop.jobs.view',
    'workshop.jobs.manage',
    'workshop.parts.manage',
    'workshop.inventory.manage',
    'workshop.invoices.manage',
    'billing.view',
    'billing.manage',
    'reports.view',
    'analytics.view',
    'branding.manage',
    'integrations.manage',
    'settings.manage',
    'audit.view',
    'audit.export',
    'support.impersonate',
]

LEGACY_PERMISSION_ALIASES: Dict[str, str] = {
    'manage_org': 'tenant.edit',
    'manage_users': 'identity.users.edit',
    'manage_catalog': 'workshop.parts.manage',
    'manage_inventory': 'workshop.inventory.manage',
    'manage_jobs': 'workshop.jobs.manage',
    'manage_customers': 'crm.customers.manage',
    'manage_billing': 'billing.manage',
    'view_reports': 'reports.view',
}

LEGACY_ROLE_ALIASES: Dict[str, str] = {
    'superuser': 'superadmin',
    'super-admin': 'superadmin',
    'superadmin': 'platform_owner',
    'platform_owner': 'platform_owner',
    'administrator': 'admin',
    'tenant_admin': 'tenant_admin',
    'owner': 'owner',
    'admin': 'admin',
    'manager': 'manager',
    'technician': 'technician',
    'parts_clerk': 'parts_clerk',
    'viewer': 'viewer',
    'customer': 'customer',
    'platform_owner': 'platform_owner',
    'platform_admin': 'platform_owner',
    'superadmin': 'platform_owner',
    'platform_support': 'admin',
    'platform_auditor': 'viewer',
}

ROLE_PERMISSION_MAP: Dict[str, List[str]] = {
    'platform_owner': [
        'identity.users.view', 'identity.users.invite', 'identity.users.edit', 'identity.users.suspend',
        'identity.memberships.manage', 'tenant.view', 'tenant.create', 'tenant.edit', 'tenant.transfer',
        'tenant.migrate', 'tenant.suspend', 'tenant.archive', 'crm.customers.view', 'crm.customers.manage',
        'vehicle.view', 'vehicle.manage', 'workshop.jobs.view', 'workshop.jobs.manage',
        'workshop.parts.manage', 'workshop.inventory.manage', 'workshop.invoices.manage', 'billing.view',
        'billing.manage', 'reports.view', 'analytics.view', 'branding.manage', 'integrations.manage',
        'settings.manage', 'audit.view', 'audit.export', 'support.impersonate',
    ],
    'superadmin': [
        'identity.users.view', 'identity.users.invite', 'identity.users.edit', 'identity.users.suspend',
        'identity.memberships.manage', 'tenant.view', 'tenant.create', 'tenant.edit', 'tenant.transfer',
        'tenant.migrate', 'tenant.suspend', 'tenant.archive', 'crm.customers.view', 'crm.customers.manage',
        'vehicle.view', 'vehicle.manage', 'workshop.jobs.view', 'workshop.jobs.manage',
        'workshop.parts.manage', 'workshop.inventory.manage', 'workshop.invoices.manage', 'billing.view',
        'billing.manage', 'reports.view', 'analytics.view', 'branding.manage', 'integrations.manage',
        'settings.manage', 'audit.view', 'audit.export', 'support.impersonate',
    ],
    'tenant_admin': [
        'identity.users.view', 'identity.users.invite', 'identity.users.edit', 'identity.memberships.manage',
        'tenant.view', 'tenant.edit', 'crm.customers.view', 'crm.customers.manage', 'vehicle.view',
        'vehicle.manage', 'workshop.jobs.view', 'workshop.jobs.manage', 'workshop.parts.manage',
        'workshop.inventory.manage', 'workshop.invoices.manage', 'billing.view', 'billing.manage',
        'reports.view', 'analytics.view', 'branding.manage', 'integrations.manage', 'settings.manage',
        'audit.view',
    ],
    'owner': [
        'identity.users.view', 'identity.users.invite', 'identity.users.edit', 'identity.memberships.manage',
        'tenant.view', 'tenant.edit', 'crm.customers.view', 'crm.customers.manage', 'vehicle.view',
        'vehicle.manage', 'workshop.jobs.view', 'workshop.jobs.manage', 'workshop.parts.manage',
        'workshop.inventory.manage', 'workshop.invoices.manage', 'billing.view', 'billing.manage',
        'reports.view', 'analytics.view', 'branding.manage', 'integrations.manage', 'settings.manage',
        'audit.view',
    ],
    'admin': [
        'identity.users.view', 'identity.users.invite', 'identity.users.edit', 'identity.memberships.manage',
        'tenant.view', 'crm.customers.view', 'crm.customers.manage', 'vehicle.view', 'vehicle.manage',
        'workshop.jobs.view', 'workshop.jobs.manage', 'workshop.parts.manage', 'workshop.inventory.manage',
        'workshop.invoices.manage', 'billing.view', 'billing.manage', 'reports.view', 'analytics.view',
        'branding.manage', 'integrations.manage', 'settings.manage', 'audit.view',
    ],
    'manager': [
        'identity.users.view', 'identity.users.invite', 'tenant.view', 'crm.customers.view', 'crm.customers.manage',
        'vehicle.view', 'vehicle.manage', 'workshop.jobs.view', 'workshop.jobs.manage', 'workshop.parts.manage',
        'workshop.inventory.manage', 'workshop.invoices.manage', 'billing.view', 'reports.view', 'analytics.view', 'audit.view',
    ],
    'technician': ['vehicle.view', 'workshop.jobs.view', 'workshop.jobs.manage', 'reports.view'],
    'parts_clerk': ['vehicle.view', 'workshop.parts.manage', 'workshop.inventory.manage', 'reports.view'],
    'viewer': ['tenant.view', 'crm.customers.view', 'vehicle.view', 'workshop.jobs.view', 'billing.view', 'reports.view', 'analytics.view', 'audit.view'],
    'customer': ['vehicle.view', 'crm.customers.view', 'workshop.jobs.view', 'billing.view'],
}

DEFAULT_PORTAL_BY_ROLE: Dict[str, str] = {
    'platform_owner': 'platform_owner',
    'superadmin': 'platform_owner',
    'tenant_admin': 'franchise',
    'owner': 'franchise',
    'admin': 'workshop',
    'manager': 'workshop',
    'technician': 'workshop',
    'parts_clerk': 'workshop',
    'viewer': 'customer',
    'customer': 'customer',
}

FEATURE_FLAGS: List[str] = [
    'white_label', 'customer_portal', 'fleet_portal', 'enterprise_fleet', 'advanced_analytics',
    'online_payments', 'sms_notifications', 'whatsapp_notifications', 'api_access', 'multi_workshop',
]


def normalize_role_name(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = str(role).strip().lower()
    if not normalized:
        return None
    return LEGACY_ROLE_ALIASES.get(normalized, normalized)


def normalize_permission_name(permission: str | None) -> str | None:
    if permission is None:
        return None
    normalized = str(permission).strip().lower().replace(' ', '_')
    if not normalized:
        return None
    return LEGACY_PERMISSION_ALIASES.get(normalized, normalized)


def permissions_for_role(role: str | None) -> Set[str]:
    normalized = normalize_role_name(role)
    if not normalized:
        return set()
    return set(ROLE_PERMISSION_MAP.get(normalized, []))


def portal_for_role(role: str | None) -> str:
    normalized = normalize_role_name(role) or 'viewer'
    return DEFAULT_PORTAL_BY_ROLE.get(normalized, 'customer')
