"""SuperAdmin platform administration views."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
import logging

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func

from app.extensions import db
from app.models.audit_log import AuditLog
from app.services.audit_service import audit_service
from app.models.customer import Customer
from app.models.job import Job
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.user import User
from app.models.vehicle import Vehicle
from app.utils.decorators import handle_database_errors, log_function_call, login_required, validate_pagination, superadmin_required
from app.utils.roles import is_superadmin_session

superadmin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)


def _audit(action: str, entity_type: str, entity_id: Optional[int] = None, *, tenant_id: Optional[int] = None, old_values=None, new_values=None) -> None:
    """Record a platform-wide audit event."""
    audit = audit_service.record_event(
        action,
        entity_type,
        entity_id,
        tenant_id=tenant_id,
        old_values=old_values,
        new_values=new_values,
    )
    if audit is None:
        logger.error(f"Failed to write audit event {action} for {entity_type}:{entity_id}")


def _platform_counts() -> dict:
    """Return platform-wide summary statistics."""
    total_tenants = db.session.execute(db.select(func.count()).select_from(Tenant)).scalar() or 0
    active_tenants = db.session.execute(
        db.select(func.count()).select_from(Tenant).where(Tenant.status == Tenant.STATUS_ACTIVE)
    ).scalar() or 0
    suspended_tenants = db.session.execute(
        db.select(func.count()).select_from(Tenant).where(Tenant.status == Tenant.STATUS_SUSPENDED)
    ).scalar() or 0
    total_users = db.session.execute(db.select(func.count()).select_from(User)).scalar() or 0
    superadmins = db.session.execute(db.select(func.count()).select_from(User).where(User.is_superadmin == True)).scalar() or 0
    customers = db.session.execute(db.select(func.count()).select_from(User).where(User.customer_id.isnot(None))).scalar() or 0
    total_customers = db.session.execute(db.select(func.count()).select_from(Customer)).scalar() or 0
    total_vehicles = db.session.execute(db.select(func.count()).select_from(Vehicle)).scalar() or 0
    total_jobs = db.session.execute(db.select(func.count()).select_from(Job)).scalar() or 0
    jobs_by_status = {
        status: db.session.execute(
            db.select(func.count()).select_from(Job).where(Job.status == status)
        ).scalar() or 0
        for status in Job.VALID_STATUSES
    }
    total_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Job.total_cost), 0)).select_from(Job)
    ).scalar() or 0

    return {
        'total_tenants': total_tenants,
        'active_tenants': active_tenants,
        'suspended_tenants': suspended_tenants,
        'total_users': total_users,
        'superadmins': superadmins,
        'customer_users': customers,
        'total_customers': total_customers,
        'total_vehicles': total_vehicles,
        'total_jobs': total_jobs,
        'total_revenue': float(total_revenue or 0),
        'jobs_by_status': jobs_by_status,
    }


def _tenant_metrics() -> list[dict]:
    tenants = db.session.execute(db.select(Tenant).order_by(Tenant.name.asc())).scalars().all()
    rows = []
    for tenant in tenants:
        customer_count = db.session.execute(
            db.select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant.tenant_id)
        ).scalar() or 0
        vehicle_count = db.session.execute(
            db.select(func.count()).select_from(Vehicle).join(Customer, Customer.customer_id == Vehicle.customer_id).where(Customer.tenant_id == tenant.tenant_id)
        ).scalar() or 0
        active_jobs = db.session.execute(
            db.select(func.count()).select_from(Job).where(
                Job.tenant_id == tenant.tenant_id,
                Job.completed == False,
            )
        ).scalar() or 0
        revenue = db.session.execute(
            db.select(func.coalesce(func.sum(Job.total_cost), 0)).select_from(Job).where(Job.tenant_id == tenant.tenant_id)
        ).scalar() or 0
        user_count = db.session.execute(
            db.select(func.count()).select_from(TenantMembership).where(
                TenantMembership.tenant_id == tenant.tenant_id,
                TenantMembership.status == TenantMembership.STATUS_ACTIVE,
            )
        ).scalar() or 0
        rows.append({
            'tenant': tenant,
            'customer_count': customer_count,
            'vehicle_count': vehicle_count,
            'active_jobs': active_jobs,
            'revenue': float(revenue or 0),
            'user_count': user_count,
        })
    return rows


@superadmin_bp.route('/home')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def home():
    return redirect(url_for('platform.home'))


@superadmin_bp.route('/tenants', methods=['GET', 'POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def tenants():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        owner_user_id = request.form.get('owner_user_id', type=int)
        business_type = (request.form.get('business_type') or Tenant.TYPE_AUTO_REPAIR).strip()
        email = (request.form.get('email') or '').strip() or None
        phone = (request.form.get('phone') or '').strip() or None
        address = (request.form.get('address') or '').strip() or None

        if not name:
            flash('Tenant name is required', 'error')
            return redirect(url_for('admin.tenants'))
        if business_type not in Tenant.VALID_TYPES:
            flash('Invalid business type', 'error')
            return redirect(url_for('admin.tenants'))

        tenant = Tenant(
            name=name,
            slug=Tenant.generate_slug(name),
            business_type=business_type,
            email=email,
            phone=phone,
            address=address,
            status=Tenant.STATUS_TRIAL,
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
            settings={'currency': 'ZAR', 'tax_rate': 0.0},
        )
        db.session.add(tenant)
        db.session.flush()

        subscription = Subscription(
            tenant_id=tenant.tenant_id,
            plan=Subscription.PLAN_FREE,
            status=Subscription.STATUS_TRIALING,
            trial_ends_at=tenant.trial_ends_at,
        )
        db.session.add(subscription)

        if owner_user_id:
            owner = User.find_by_id(owner_user_id)
            if owner:
                existing = db.session.execute(
                    db.select(TenantMembership).where(
                        TenantMembership.user_id == owner_user_id,
                        TenantMembership.tenant_id == tenant.tenant_id,
                    )
                ).scalar_one_or_none()
                if not existing:
                    membership = TenantMembership(
                        user_id=owner_user_id,
                        tenant_id=tenant.tenant_id,
                        role=TenantMembership.ROLE_OWNER,
                        status=TenantMembership.STATUS_ACTIVE,
                        is_default=True,
                    )
                    db.session.add(membership)
        db.session.commit()
        _audit('tenant_created', 'tenant', tenant.tenant_id, new_values=tenant.to_dict())
        flash('Tenant created successfully', 'success')
        return redirect(url_for('admin.tenant_detail', tenant_id=tenant.tenant_id))

    tenants = db.session.execute(db.select(Tenant).order_by(Tenant.name.asc())).scalars().all()
    metrics = {row['tenant'].tenant_id: row for row in _tenant_metrics()}
    owners = db.session.execute(
        db.select(User).order_by(User.username.asc())
    ).scalars().all()
    return render_template('superadmin/tenants.html', tenants=tenants, metrics=metrics, owners=owners)


@superadmin_bp.route('/tenants/<int:tenant_id>', methods=['GET', 'POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def tenant_detail(tenant_id: int):
    tenant = Tenant.find_by_id(tenant_id)
    if not tenant:
        flash('Tenant not found', 'error')
        return redirect(url_for('admin.tenants'))

    if request.method == 'POST':
        old_values = tenant.to_dict()
        new_name = (request.form.get('name') or tenant.name).strip()
        if new_name != tenant.name:
            tenant.slug = Tenant.generate_slug(new_name)
        tenant.name = new_name
        tenant.business_type = (request.form.get('business_type') or tenant.business_type).strip()
        tenant.email = (request.form.get('email') or '').strip() or None
        tenant.phone = (request.form.get('phone') or '').strip() or None
        tenant.address = (request.form.get('address') or '').strip() or None
        tenant.status = (request.form.get('status') or tenant.status).strip()
        settings = tenant.settings or {}
        settings['currency'] = (request.form.get('currency') or settings.get('currency') or 'ZAR').strip().upper()
        tenant.settings = settings
        db.session.commit()
        _audit('tenant_updated', 'tenant', tenant.tenant_id, old_values=old_values, new_values=tenant.to_dict())
        flash('Tenant updated successfully', 'success')
        return redirect(url_for('admin.tenant_detail', tenant_id=tenant.tenant_id))

    tenant_customers = db.session.execute(
        db.select(Customer).where(Customer.tenant_id == tenant.tenant_id).order_by(Customer.family_name.asc(), Customer.first_name.asc())
    ).scalars().all()
    tenant_jobs = db.session.execute(
        db.select(Job).where(Job.tenant_id == tenant.tenant_id).order_by(Job.job_date.desc(), Job.job_id.desc()).limit(25)
    ).scalars().all()
    tenant_users = db.session.execute(
        db.select(TenantMembership).where(TenantMembership.tenant_id == tenant.tenant_id).order_by(TenantMembership.id.asc())
    ).scalars().all()
    recent_audit = db.session.execute(
        db.select(AuditLog).where(AuditLog.tenant_id == tenant.tenant_id).order_by(AuditLog.created_at.desc()).limit(15)
    ).scalars().all()
    metrics = next((row for row in _tenant_metrics() if row['tenant'].tenant_id == tenant.tenant_id), None)
    return render_template(
        'superadmin/tenant_detail.html',
        tenant=tenant,
        tenant_customers=tenant_customers,
        tenant_jobs=tenant_jobs,
        tenant_users=tenant_users,
        recent_audit=recent_audit,
        metrics=metrics,
    )


@superadmin_bp.route('/tenants/<int:tenant_id>/suspend', methods=['POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def suspend_tenant(tenant_id: int):
    tenant = Tenant.find_by_id(tenant_id)
    if not tenant:
        flash('Tenant not found', 'error')
        return redirect(url_for('admin.tenants'))
    old_values = tenant.to_dict()
    tenant.status = Tenant.STATUS_SUSPENDED
    db.session.commit()
    _audit('tenant_suspended', 'tenant', tenant.tenant_id, old_values=old_values, new_values=tenant.to_dict())
    flash('Tenant suspended', 'success')
    return redirect(url_for('admin.tenant_detail', tenant_id=tenant.tenant_id))


@superadmin_bp.route('/tenants/<int:tenant_id>/reactivate', methods=['POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def reactivate_tenant(tenant_id: int):
    tenant = Tenant.find_by_id(tenant_id)
    if not tenant:
        flash('Tenant not found', 'error')
        return redirect(url_for('admin.tenants'))
    old_values = tenant.to_dict()
    tenant.status = Tenant.STATUS_ACTIVE
    db.session.commit()
    _audit('tenant_reactivated', 'tenant', tenant.tenant_id, old_values=old_values, new_values=tenant.to_dict())
    flash('Tenant reactivated', 'success')
    return redirect(url_for('admin.tenant_detail', tenant_id=tenant.tenant_id))


@superadmin_bp.route('/users', methods=['GET', 'POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def users():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower() or None
        role = (request.form.get('role') or '').strip() or 'customer'
        is_superadmin = bool(request.form.get('is_superadmin'))
        preferred_tenant_id = request.form.get('preferred_tenant_id', type=int)
        customer_id = request.form.get('customer_id', type=int)
        is_active = request.form.get('is_active') is not None

        if not username:
            flash('Username is required', 'error')
            return redirect(url_for('admin.users'))

        normalized_role = 'platform_owner' if is_superadmin else role
        user = User(
            username=username,
            email=email,
            role=normalized_role,
            is_superadmin=is_superadmin,
            is_active=is_active,
            preferred_tenant_id=preferred_tenant_id,
            customer_id=customer_id,
            email_verified=False,
        )
        db.session.add(user)
        db.session.commit()
        _audit('user_created', 'user', user.user_id, new_values=user.to_dict(include_sensitive=True))
        flash('User created successfully', 'success')
        return redirect(url_for('admin.users'))

    role_filter = (request.args.get('role') or '').strip().lower()
    status_filter = (request.args.get('status') or '').strip().lower()
    query = db.select(User).order_by(User.username.asc())
    users_list = db.session.execute(query).scalars().all()
    if role_filter:
        if role_filter in {'superadmin', 'platform_owner'}:
            users_list = [u for u in users_list if u.is_superadmin]
        elif role_filter == 'customer':
            users_list = [u for u in users_list if getattr(u, 'customer_id', None)]
        else:
            users_list = [u for u in users_list if (u.role or '').lower() == role_filter]
    if status_filter == 'active':
        users_list = [u for u in users_list if u.is_active]
    elif status_filter == 'inactive':
        users_list = [u for u in users_list if not u.is_active]

    tenants = db.session.execute(db.select(Tenant).order_by(Tenant.name.asc())).scalars().all()
    customers = db.session.execute(db.select(Customer).order_by(Customer.family_name.asc(), Customer.first_name.asc())).scalars().all()
    return render_template(
        'superadmin/users.html',
        users=users_list,
        tenants=tenants,
        customers=customers,
        role_filter=role_filter,
        status_filter=status_filter,
    )


@superadmin_bp.route('/users/<int:user_id>', methods=['POST'])
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def update_user(user_id: int):
    user = User.find_by_id(user_id)
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('admin.users'))

    old_values = user.to_dict(include_sensitive=True)
    user.username = (request.form.get('username') or user.username).strip()
    user.email = (request.form.get('email') or '').strip().lower() or None
    user.is_superadmin = bool(request.form.get('is_superadmin'))
    user.role = 'platform_owner' if user.is_superadmin else (request.form.get('role') or user.role or 'customer').strip()
    user.is_active = bool(request.form.get('is_active'))
    user.preferred_tenant_id = request.form.get('preferred_tenant_id', type=int)
    user.customer_id = request.form.get('customer_id', type=int)
    db.session.commit()
    _audit('user_updated', 'user', user.user_id, old_values=old_values, new_values=user.to_dict(include_sensitive=True))
    flash('User updated successfully', 'success')
    return redirect(url_for('admin.users'))


@superadmin_bp.route('/roles')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def roles():
    role_rows = [
        {
            'role': 'platform_owner',
            'scope': 'Platform-wide',
            'description': 'Full access to all tenants, data, users and settings.',
        },
        {
            'role': 'tenant_admin',
            'scope': 'Single tenant',
            'description': 'Workshop administration inside one tenant. Phase 2B extends this role.',
        },
        {
            'role': 'customer',
            'scope': 'Own account',
            'description': 'Portal access to own vehicles, bookings and service history.',
        },
    ]
    return render_template('superadmin/roles.html', role_rows=role_rows)


@superadmin_bp.route('/audit')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def audit():
    audit_rows = db.session.execute(
        db.select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.audit_log_id.desc()).limit(200)
    ).scalars().all()
    return render_template('superadmin/audit.html', audit_rows=audit_rows)


@superadmin_bp.route('/reports')
@login_required
@superadmin_required
@handle_database_errors
@log_function_call
def reports():
    summary = _platform_counts()
    monthly_jobs = summary['jobs_by_status']
    active_tenants = summary['active_tenants']
    return render_template(
        'superadmin/reports.html',
        summary=summary,
        monthly_jobs=monthly_jobs,
        active_tenants=active_tenants,
    )
