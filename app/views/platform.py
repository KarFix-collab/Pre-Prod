"""Platform owner portal foundation routes."""
from __future__ import annotations

from datetime import date
import logging
from typing import Any

from flask import Blueprint, redirect, render_template, url_for
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.job import Job
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.user import User
from app.models.vehicle import Vehicle
from app.utils.decorators import handle_database_errors, log_function_call, login_required, superadmin_required

platform_bp = Blueprint('platform', __name__)
logger = logging.getLogger(__name__)

PLAN_LABELS = {
    Subscription.PLAN_FREE: 'Free',
    Subscription.PLAN_STARTER: 'Starter',
    Subscription.PLAN_PROFESSIONAL: 'Professional',
    Subscription.PLAN_ENTERPRISE: 'Enterprise',
}

TENANT_TYPE_LABELS = {
    Tenant.TYPE_AUTO_REPAIR: 'Auto Repair',
    Tenant.TYPE_PARTS_SELLER: 'Parts Seller',
    Tenant.TYPE_BOTH: 'Auto Repair + Parts',
}

PLATFORM_LAUNCHPAD = [
    {
        'group': 'Operations',
        'cards': [
            {
                'title': 'Tenant Management',
                'description': 'Manage franchises, workshop onboarding, ownership, and lifecycle events.',
                'route': 'admin.tenants',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-building-skyscraper',
            },
            {
                'title': 'Franchise Management',
                'description': 'Branding, billing, workshop assignment, and franchise controls.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-building-castle',
            },
            {
                'title': 'Workshop Management',
                'description': 'Monitor workshops, onboarding, health, and operational performance.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-tools',
            },
            {
                'title': 'Enterprise Fleet',
                'description': 'Track enterprise accounts, national contracts, and fleet visibility.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-truck',
            },
        ],
    },
    {
        'group': 'Identity & Access',
        'cards': [
            {
                'title': 'User Directory',
                'description': 'Review platform users, memberships, and account status.',
                'route': 'admin.users',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-users',
            },
            {
                'title': 'Roles',
                'description': 'Inspect assigned roles and access patterns across the platform.',
                'route': 'admin.roles',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-shield-lock',
            },
            {
                'title': 'Permissions',
                'description': 'Review the canonical permission catalogue and policy boundaries.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-key',
            },
            {
                'title': 'Memberships',
                'description': 'Manage the relationship between identities and tenant scopes.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-id',
            },
        ],
    },
    {
        'group': 'Commercial',
        'cards': [
            {
                'title': 'Subscription Management',
                'description': 'Review plans, subscription status, and commercial controls.',
                'route': 'administrator.subscription_management',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-credit-card',
            },
            {
                'title': 'Billing Console',
                'description': 'Open platform billing, revenue, and tenant payment views.',
                'route': 'administrator.billing_management',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-receipt-2',
            },
            {
                'title': 'Invoices',
                'description': 'Inspect and reconcile invoice records across the platform.',
                'route': 'administrator.invoices',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-file-invoice',
            },
        ],
    },
    {
        'group': 'Platform Intelligence',
        'cards': [
            {
                'title': 'Platform Reports',
                'description': 'View operational reporting, trends, and aggregated analytics.',
                'route': 'admin.reports',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-chart-bar',
            },
            {
                'title': 'Platform Analytics',
                'description': 'Track growth, activity, and platform health over time.',
                'route': 'admin.reports',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-chart-pie',
            },
            {
                'title': 'Insights',
                'description': 'Reserved for future forecasting and executive intelligence.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-scan',
            },
        ],
    },
    {
        'group': 'Security & Governance',
        'cards': [
            {
                'title': 'Audit Log',
                'description': 'Trace high-risk platform activity and privileged operations.',
                'route': 'admin.audit',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-shield-check',
            },
            {
                'title': 'Support Centre',
                'description': 'Reserved for platform support, diagnostics, and impersonation.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-headset',
            },
        ],
    },
    {
        'group': 'Platform Services',
        'cards': [
            {
                'title': 'Platform Settings',
                'description': 'Control global settings, release behaviour, and defaults.',
                'route': 'administrator.org_settings',
                'status': 'Live',
                'status_class': 'bg-success-lt text-success',
                'icon': 'ti-settings',
            },
            {
                'title': 'Branding',
                'description': 'Configure white-label branding, logos, email, and templates.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-palette',
            },
            {
                'title': 'Integrations',
                'description': 'Connect payment, messaging, accounting, and external services.',
                'route': None,
                'status': 'Coming soon',
                'status_class': 'bg-secondary-lt text-secondary',
                'icon': 'ti-plug-connected',
            },
        ],
    },
]


def _tenant_type_label(tenant: Tenant) -> str:
    business_type = getattr(tenant, 'business_type', None)
    if not business_type:
        return 'Unspecified'
    return TENANT_TYPE_LABELS.get(business_type, str(business_type).replace('_', ' ').title())


def _subscription_label(tenant: Tenant) -> str:
    subscription = getattr(tenant, 'subscription', None)
    if not subscription:
        return 'Free'
    plan = PLAN_LABELS.get(
        getattr(subscription, 'plan', None),
        str(getattr(subscription, 'plan', 'free')).replace('_', ' ').title(),
    )
    status = str(getattr(subscription, 'status', '') or '').replace('_', ' ').title()
    return f'{plan} · {status}' if status else plan


def _platform_counts() -> dict:
    total_tenants = db.session.execute(db.select(func.count()).select_from(Tenant)).scalar() or 0
    active_tenants = db.session.execute(
        db.select(func.count()).select_from(Tenant).where(Tenant.status == Tenant.STATUS_ACTIVE)
    ).scalar() or 0
    total_users = db.session.execute(db.select(func.count()).select_from(User)).scalar() or 0
    total_customers = db.session.execute(db.select(func.count()).select_from(Customer)).scalar() or 0
    total_vehicles = db.session.execute(db.select(func.count()).select_from(Vehicle)).scalar() or 0
    active_jobs = db.session.execute(
        db.select(func.count()).select_from(Job).where(Job.completed == False)  # noqa: E712
    ).scalar() or 0
    active_memberships = db.session.execute(
        db.select(func.count()).select_from(TenantMembership).where(
            TenantMembership.status == TenantMembership.STATUS_ACTIVE
        )
    ).scalar() or 0
    revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Job.total_cost), 0)).select_from(Job)
    ).scalar() or 0
    return {
        'total_tenants': total_tenants,
        'active_tenants': active_tenants,
        'total_users': total_users,
        'total_customers': total_customers,
        'total_vehicles': total_vehicles,
        'active_jobs': active_jobs,
        'active_memberships': active_memberships,
        'revenue': float(revenue or 0),
    }


def _recent_audit(limit: int = 8):
    return db.session.execute(
        db.select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.audit_log_id.desc()).limit(limit)
    ).scalars().all()


def _recent_tenant_rows(limit: int = 6) -> list[dict[str, Any]]:
    tenants = db.session.execute(
        db.select(Tenant)
        .options(selectinload(Tenant.subscription))
        .order_by(Tenant.created_at.desc())
        .limit(limit)
    ).scalars().all()

    tenant_ids = [tenant.tenant_id for tenant in tenants]
    active_memberships_by_tenant: dict[int, int] = {}
    if tenant_ids:
        rows = db.session.execute(
            db.select(TenantMembership.tenant_id, func.count())
            .where(
                TenantMembership.tenant_id.in_(tenant_ids),
                TenantMembership.status == TenantMembership.STATUS_ACTIVE,
            )
            .group_by(TenantMembership.tenant_id)
        ).all()
        active_memberships_by_tenant = {tenant_id: count for tenant_id, count in rows}

    tenant_rows = []
    for tenant in tenants:
        tenant_rows.append({
            'tenant': tenant,
            'tenant_type_label': _tenant_type_label(tenant),
            'subscription_label': _subscription_label(tenant),
            'active_memberships': active_memberships_by_tenant.get(tenant.tenant_id, 0),
        })
    return tenant_rows


@platform_bp.route('/')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def home():
    summary = _platform_counts()
    recent_audit = _recent_audit()
    recent_tenants = _recent_tenant_rows()
    top_roles = db.session.execute(
        db.select(TenantMembership.role, func.count())
        .group_by(TenantMembership.role)
        .order_by(func.count().desc())
        .limit(5)
    ).all()
    return render_template(
        'platform/home.html',
        summary=summary,
        recent_audit=recent_audit,
        recent_tenants=recent_tenants,
        top_roles=top_roles,
        current_date=date.today(),
    )


@platform_bp.route('/dashboard')
@login_required
@superadmin_required
def dashboard():
    return redirect(url_for('platform.home'))


@platform_bp.route('/modules')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def modules():
    return render_template('platform/modules.html', module_groups=PLATFORM_LAUNCHPAD, current_date=date.today())
