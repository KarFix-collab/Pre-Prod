"""
Customer Portal Blueprint
Provides customer-facing dashboard, vehicle management, booking requests,
and profile management.
"""
from datetime import date
import logging

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, g, make_response

from app.extensions import db
from app.models.customer import Customer
from app.models.job import Job
from app.models.job import JobStatusHistory
from app.models.tenant import Tenant
from app.models.user import User
from app.services.customer_service import CustomerService
from app.services.billing_service import BillingService
from app.services.invoice_service import InvoiceService
from app.utils.decorators import login_required, handle_database_errors, log_function_call
from app.utils.validators import sanitize_input, validate_date, validate_positive_integer, validate_customer_data

customer_bp = Blueprint('customer', __name__)
logger = logging.getLogger(__name__)

customer_service = CustomerService()
billing_service = BillingService()


def _sync_customer_session(customer: Customer) -> None:
    """Ensure the current session reflects the portal customer and tenant."""
    tenant_id = customer.preferred_tenant_id or customer.tenant_id
    tenant = Tenant.find_by_id(tenant_id) if tenant_id else None
    if tenant:
        session['current_tenant_id'] = tenant.tenant_id
        session['current_tenant_slug'] = tenant.slug
        session['current_tenant_name'] = tenant.name
        session['preferred_tenant_id'] = tenant.tenant_id
        g.current_tenant = tenant
        g.current_tenant_id = tenant.tenant_id

    session['current_role'] = 'customer'
    session['customer_id'] = customer.customer_id
    session['customer_name'] = customer.full_name


def _get_portal_customer() -> Customer | None:
    """Resolve the logged-in user's customer record."""
    user_id = session.get('user_id')
    if not user_id:
        return None

    user = User.find_by_id(user_id)
    if not user:
        return None

    # Resolve customer using user.customer_id as the authoritative source.
    # The session customer_id is used only as a fallback for legacy sessions
    # created before migration 008 set user.customer_id.
    # This prevents a stale session from returning the wrong customer record.
    linked_customer_id = getattr(user, 'customer_id', None)
    session_customer_id = session.get('customer_id')
    resolved_customer_id = linked_customer_id or session_customer_id

    if not resolved_customer_id:
        return None

    customer = db.session.get(Customer, resolved_customer_id)
    if customer:
        _sync_customer_session(customer)
        return customer

    return None


def _customer_or_redirect():
    customer = _get_portal_customer()
    if not customer:
        flash('No customer profile is linked to this account yet.', 'warning')
        return None, redirect(url_for('main.index'))
    return customer, None


def _workshop_options(customer: Customer):
    return customer_service.get_workshop_options_for_email(
        customer.email or '',
        selected_tenant_id=customer.preferred_tenant_id,
    )


def _switch_workshop_for_customer(customer: Customer, tenant_id: int):
    """Set the customer portal's preferred workshop."""
    if not tenant_id:
        return False, ['Please select a workshop'], customer

    success, errors, updated_customer = customer_service.set_customer_preferred_tenant(customer.customer_id, tenant_id)
    if success and updated_customer:
        _sync_customer_session(updated_customer)
        return True, [], updated_customer
    return False, errors, customer


def _resolve_customer_job(customer: Customer, job_id: int):
    """Resolve a job and ensure it belongs to the current portal customer."""
    job = Job.find_by_id(job_id)
    if not job or job.customer != customer.customer_id:
        return None
    return job


def _job_service_rows(job: Job):
    """Return a readable list of services attached to a job."""
    rows = []
    for entry in getattr(job, 'job_services', []) or []:
        service = getattr(entry, 'service', None)
        if service is None:
            continue
        service_name = getattr(service, 'service_name', None) or getattr(service, 'name', None) or 'Service'
        rows.append({
            'name': service_name,
            'qty': entry.qty,
            'unit_cost': getattr(service, 'cost', 0),
            'total_cost': entry.total_cost,
        })
    return rows


def _job_part_rows(job: Job):
    """Return a readable list of parts attached to a job."""
    rows = []
    for entry in getattr(job, 'job_parts', []) or []:
        part = getattr(entry, 'part', None)
        if part is None:
            continue
        part_name = getattr(part, 'part_name', None) or getattr(part, 'name', None) or getattr(part, 'description', None) or 'Part'
        rows.append({
            'name': part_name,
            'qty': entry.qty,
            'unit_cost': getattr(part, 'cost', 0),
            'total_cost': entry.total_cost,
        })
    return rows


@customer_bp.route('/')
@customer_bp.route('/dashboard')
@login_required
@handle_database_errors
@log_function_call
def dashboard():
    """Customer dashboard overview."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    try:
        dashboard = customer_service.get_customer_dashboard_context(customer.customer_id)
        return render_template(
            'customer/dashboard.html',
            customer=customer,
            stats=dashboard.get('stats', {}),
            recent_jobs=dashboard.get('recent_jobs', []),
            upcoming_jobs=dashboard.get('upcoming_jobs', []),
            unpaid_jobs=dashboard.get('unpaid_jobs', []),
            overdue_count=dashboard.get('overdue_count', 0),
            vehicles=dashboard.get('vehicles', []),
            vehicle_histories=dashboard.get('vehicle_histories', {}),
            vehicle_summaries=dashboard.get('vehicle_summaries', []),
            preferred_workshop=dashboard.get('preferred_workshop'),
            primary_vehicle=dashboard.get('primary_vehicle'),
            last_service_job=dashboard.get('last_service_job'),
            last_service_date=dashboard.get('last_service_date'),
            last_service_vehicle=dashboard.get('last_service_vehicle'),
            active_jobs=dashboard.get('active_jobs', 0),
            upcoming_jobs_count=dashboard.get('upcoming_jobs_count', 0),
            workshop_options=_workshop_options(customer),
            min_date=date.today().isoformat(),
        )
    except Exception as e:
        logger.error(f'Customer dashboard loading failed: {e}')
        flash('Failed to load your dashboard', 'error')
        return render_template(
            'customer/dashboard.html',
            customer=customer,
            stats={},
            recent_jobs=[],
            upcoming_jobs=[],
            unpaid_jobs=[],
            overdue_count=0,
            vehicles=[],
            vehicle_histories={},
            vehicle_summaries=[],
            preferred_workshop=None,
            primary_vehicle=None,
            last_service_job=None,
            last_service_date=None,
            last_service_vehicle=None,
            active_jobs=0,
            upcoming_jobs_count=0,
            workshop_options=[],
            min_date=date.today().isoformat(),
        )


@customer_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@handle_database_errors
@log_function_call
def profile():
    """Customer profile page."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    workshop_options = _workshop_options(customer)
    if request.method == 'POST':
        profile_data = {
            'first_name': sanitize_input(request.form.get('first_name', '')),
            'family_name': sanitize_input(request.form.get('family_name', '')),
            'phone': sanitize_input(request.form.get('phone', '')),
        }

        # Keep the login email fixed to preserve the linked portal account.
        profile_data['email'] = customer.email

        validation_result = validate_customer_data({
            'first_name': profile_data['first_name'],
            'family_name': profile_data['family_name'],
            'email': profile_data['email'],
            'phone': profile_data['phone'],
        })
        if not validation_result.is_valid:
            for error in validation_result.get_errors():
                flash(error, 'error')
            return render_template('customer/profile.html', customer=customer, workshop_options=workshop_options)

        success, errors, updated_customer = customer_service.update_customer(customer.customer_id, profile_data)
        if success:
            _sync_customer_session(updated_customer)
            flash('Your profile was updated.', 'success')
            return redirect(url_for('customer.profile'))

        for error in errors:
            flash(error, 'error')

    return render_template('customer/profile.html', customer=customer, workshop_options=workshop_options)


@customer_bp.route('/workshop', methods=['POST'])
@login_required
@handle_database_errors
def set_preferred_workshop():
    """Set the customer portal's preferred workshop."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    tenant_id = request.form.get('tenant_id', type=int)
    success, errors, _ = _switch_workshop_for_customer(customer, tenant_id)
    if success:
        flash('Preferred workshop updated.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(request.referrer or url_for('customer.dashboard'))


@customer_bp.route('/vehicles')
@login_required
@handle_database_errors
@log_function_call
def vehicles():
    """Vehicle management page."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    return render_template(
        'customer/vehicles.html',
        customer=customer,
        vehicles=customer.get_vehicles(),
        vehicle_histories={vehicle.vehicle_id: customer_service.get_vehicle_history(vehicle.vehicle_id) for vehicle in customer.get_vehicles()},
        min_date=date.today().isoformat(),
    )


@customer_bp.route('/vehicles', methods=['POST'])
@login_required
@handle_database_errors
@log_function_call
def create_vehicle():
    """Add a vehicle for the current customer."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    vehicle_data = {
        'customer_id': customer.customer_id,
        'make': sanitize_input(request.form.get('make', '')),
        'model': sanitize_input(request.form.get('model', '')),
        'year': request.form.get('year', type=int) or None,
        'registration_number': sanitize_input(request.form.get('registration_number', '')).strip() or None,
        'vin': sanitize_input(request.form.get('vin', '')).strip() or None,
        'color': sanitize_input(request.form.get('color', '')).strip() or None,
        'mileage': request.form.get('mileage', type=int) or None,
        'notes': sanitize_input(request.form.get('notes', '')).strip() or None,
        'is_primary': bool(request.form.get('is_primary')),
    }

    success, errors, vehicle = customer_service.create_vehicle(vehicle_data)
    if success:
        flash(f'Vehicle {vehicle.display_name} added successfully.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(url_for('customer.vehicles'))


@customer_bp.route('/vehicles/<int:vehicle_id>/update', methods=['POST'])
@login_required
@handle_database_errors
@log_function_call
def update_vehicle(vehicle_id: int):
    """Update a customer vehicle."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    vehicle = next((v for v in customer.get_vehicles() if v.vehicle_id == vehicle_id), None)
    if not vehicle:
        flash('Vehicle not found.', 'error')
        return redirect(url_for('customer.vehicles'))

    vehicle_data = {
        'make': sanitize_input(request.form.get('make', '')),
        'model': sanitize_input(request.form.get('model', '')),
        'year': request.form.get('year', type=int) or None,
        'registration_number': sanitize_input(request.form.get('registration_number', '')).strip() or None,
        'vin': sanitize_input(request.form.get('vin', '')).strip() or None,
        'color': sanitize_input(request.form.get('color', '')).strip() or None,
        'mileage': request.form.get('mileage', type=int) or None,
        'notes': sanitize_input(request.form.get('notes', '')).strip() or None,
        'is_primary': bool(request.form.get('is_primary')),
    }

    success, errors, _ = customer_service.update_vehicle(vehicle_id, vehicle_data)
    if success:
        flash('Vehicle updated successfully.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(url_for('customer.vehicles'))


@customer_bp.route('/vehicles/<int:vehicle_id>/delete', methods=['POST'])
@login_required
@handle_database_errors
@log_function_call
def delete_vehicle(vehicle_id: int):
    """Delete a vehicle."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    vehicle = next((v for v in customer.get_vehicles() if v.vehicle_id == vehicle_id), None)
    if not vehicle:
        flash('Vehicle not found.', 'error')
        return redirect(url_for('customer.vehicles'))

    success, errors = customer_service.delete_vehicle(vehicle_id)
    if success:
        flash('Vehicle removed.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(url_for('customer.vehicles'))


@customer_bp.route('/vehicles/<int:vehicle_id>/primary', methods=['POST'])
@login_required
@handle_database_errors
@log_function_call
def set_primary_vehicle(vehicle_id: int):
    """Mark a vehicle as the primary vehicle."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    vehicle = next((v for v in customer.get_vehicles() if v.vehicle_id == vehicle_id), None)
    if not vehicle:
        flash('Vehicle not found.', 'error')
        return redirect(url_for('customer.vehicles'))

    success, errors, _ = customer_service.update_vehicle(vehicle_id, {'is_primary': True})
    if success:
        flash('Primary vehicle updated.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(url_for('customer.vehicles'))


@customer_bp.route('/jobs', methods=['GET', 'POST'])
@login_required
@handle_database_errors
@log_function_call
def jobs():
    """Customer job list and service request form."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    vehicles = customer.get_vehicles()
    if request.method == 'POST':
        job_date_str = sanitize_input(request.form.get('job_date', ''))
        vehicle_id = request.form.get('vehicle_id', type=int)
        selected_tenant_id = request.form.get('tenant_id', type=int)
        if not job_date_str or not validate_date(job_date_str):
            flash('Please choose a valid service date.', 'error')
            return redirect(url_for('customer.jobs'))

        if vehicles and not vehicle_id:
            flash('Please select a vehicle for this booking.', 'error')
            return redirect(url_for('customer.jobs'))

        # Verify the submitted vehicle_id belongs to this customer
        if vehicle_id:
            owned_ids = {v.vehicle_id for v in vehicles}
            if vehicle_id not in owned_ids:
                flash('Invalid vehicle selection.', 'error')
                return redirect(url_for('customer.jobs'))

        job_date = date.fromisoformat(job_date_str)
        success, errors, job_id = customer_service.schedule_job_for_customer(
            customer.customer_id,
            job_date,
            vehicle_id=vehicle_id,
            tenant_id=selected_tenant_id,
        )
        if success:
            flash('Your service request has been submitted.', 'success')
            return redirect(url_for('customer.jobs'))

        for error in errors:
            flash(error, 'error')

    customer_jobs = customer_service.get_customer_jobs(customer.customer_id)
    return render_template(
        'customer/jobs.html',
        customer=customer,
        jobs=customer_jobs,
        vehicles=vehicles,
        workshop_options=_workshop_options(customer),
        preferred_workshop=customer.preferred_tenant or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None),
        min_date=date.today().isoformat(),
    )


@customer_bp.route('/history')
@login_required
@handle_database_errors
@log_function_call
def history():
    """Customer service history page."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    try:
        jobs = customer_service.get_customer_jobs(customer.customer_id)
        completed_jobs = [job for job in jobs if job.completed]
        service_history = []
        for job in jobs:
            service_history.append({
                'job': job,
                'vehicle': job.vehicle_rel,
                'services': _job_service_rows(job),
                'parts': _job_part_rows(job),
                'status_history': list(getattr(job, 'status_history', []) or []),
                'attachments': list(getattr(job, 'attachments', []) or []),
            })

        return render_template(
            'customer/history.html',
            customer=customer,
            jobs=jobs,
            completed_jobs=completed_jobs,
            service_history=service_history,
            preferred_workshop=customer.preferred_tenant or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None),
            workshop_options=_workshop_options(customer),
        )
    except Exception as exc:
        logger.error(f'Customer history loading failed: {exc}')
        flash('Failed to load your service history', 'error')
        return render_template(
            'customer/history.html',
            customer=customer,
            jobs=[],
            completed_jobs=[],
            service_history=[],
            preferred_workshop=customer.preferred_tenant or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None),
            workshop_options=_workshop_options(customer),
        )


@customer_bp.route('/jobs/<int:job_id>')
@login_required
@handle_database_errors
@log_function_call
def job_detail(job_id: int):
    """Display a single customer job with its service history."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    job = _resolve_customer_job(customer, job_id)
    if not job:
        flash('Job not found.', 'error')
        return redirect(url_for('customer.jobs'))

    job_status_history = list(getattr(job, 'status_history', []) or [])
    return render_template(
        'customer/job_detail.html',
        customer=customer,
        job=job,
        services=_job_service_rows(job),
        parts=_job_part_rows(job),
        status_history=job_status_history,
        attachments=list(getattr(job, 'attachments', []) or []),
        preferred_workshop=customer.preferred_tenant or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None),
        workshop_options=_workshop_options(customer),
    )



@customer_bp.route('/invoices')
@login_required
@handle_database_errors
@log_function_call
def invoices():
    """Customer invoice center."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    try:
        invoice_service = InvoiceService()
        invoices = invoice_service.get_invoices_for_customer(customer.customer_id)
        summary = {
            'total': len(invoices),
            'unpaid': len([i for i in invoices if i.status != i.STATUS_PAID]),
            'paid': len([i for i in invoices if i.status == i.STATUS_PAID]),
            'overdue': len([i for i in invoices if i.is_overdue]),
            'outstanding': sum(float(i.total_amount or 0) for i in invoices if i.status != i.STATUS_PAID),
        }
        return render_template('customer/invoices.html', customer=customer, invoices=invoices, summary=summary)
    except Exception as exc:
        logger.error(f'Customer invoices loading failed: {exc}')
        flash('Failed to load your invoices', 'error')
        return render_template('customer/invoices.html', customer=customer, invoices=[], summary={'total': 0, 'unpaid': 0, 'paid': 0, 'overdue': 0, 'outstanding': 0})


@customer_bp.route('/invoices/<int:invoice_id>')
@login_required
@handle_database_errors
@log_function_call
def invoice_detail(invoice_id: int):
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response
    invoice = InvoiceService().get_invoice_by_id(invoice_id)
    if not invoice or invoice.customer_id != customer.customer_id:
        flash('Invoice not found.', 'error')
        return redirect(url_for('customer.invoices'))
    return render_template('customer/invoice_detail.html', customer=customer, invoice=invoice)


@customer_bp.route('/invoices/<int:invoice_id>/download')
@login_required
@handle_database_errors
@log_function_call
def invoice_download(invoice_id: int):
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response
    invoice = InvoiceService().get_invoice_by_id(invoice_id)
    if not invoice or invoice.customer_id != customer.customer_id:
        flash('Invoice not found.', 'error')
        return redirect(url_for('customer.invoices'))
    response = make_response(render_template('customer/invoice_download.html', invoice=invoice))
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename={invoice.invoice_number}.html'
    return response


@customer_bp.route('/billing')
@login_required
@handle_database_errors
@log_function_call
def billing():
    """Customer billing page."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    try:
        jobs = customer.get_jobs(completed_only=True)
        unpaid_jobs = customer.get_unpaid_jobs()
        paid_jobs = [job for job in jobs if job.paid]
        invoices = {job.job_id: getattr(job, 'invoice', None) for job in jobs}
        summary = billing_service.get_customer_billing_summary(customer.customer_id) or {}

        # Keep backward compatibility with older templates that referenced a dict-like object.
        summary.setdefault('total_unpaid_amount', summary.get('unpaid_amount', 0))
        summary.setdefault('total_amount', summary.get('total_amount', 0))
        summary.setdefault('overdue_jobs', summary.get('overdue_jobs', 0))
        summary.setdefault('unpaid_jobs', summary.get('unpaid_jobs', len(unpaid_jobs)))

        return render_template(
            'customer/billing.html',
            customer=customer,
            jobs=jobs,
            unpaid_jobs=unpaid_jobs,
            paid_jobs=paid_jobs,
            summary=summary,
            invoices=invoices,
        )
    except Exception as exc:
        logger.error(f'Customer billing loading failed: {exc}')
        flash('Failed to load your billing page', 'error')
        return render_template(
            'customer/billing.html',
            customer=customer,
            jobs=[],
            unpaid_jobs=[],
            paid_jobs=[],
            summary={'total_unpaid_amount': 0, 'unpaid_amount': 0, 'unpaid_jobs': 0, 'overdue_jobs': 0, 'total_amount': 0},
            invoices={},
        )


@customer_bp.route('/billing/<int:job_id>/pay', methods=['POST'])
@login_required
@handle_database_errors
@log_function_call
def pay_job(job_id: int):
    """Mark one of the customer's unpaid jobs as paid."""
    customer, redirect_response = _customer_or_redirect()
    if redirect_response:
        return redirect_response

    job = Job.find_by_id(job_id)
    if not job or job.customer != customer.customer_id:
        flash('That bill was not found.', 'error')
        return redirect(url_for('customer.billing'))

    success, errors = billing_service.mark_job_as_paid(job_id)
    if success:
        flash('Payment recorded successfully.', 'success')
    else:
        for error in errors:
            flash(error, 'error')
    return redirect(url_for('customer.billing'))
