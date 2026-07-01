"""
Administrator Routes Blueprint
Contains customer management, billing management, overdue bill handling,
organization settings, team management, service/parts catalog, and inventory
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, g, make_response
from collections import defaultdict
import csv
import io
from datetime import date, datetime, timedelta
import logging
from app.services.customer_service import CustomerService
from app.services.job_service import JobService
from app.services.billing_service import BillingService
from app.services.invoice_service import InvoiceService
from app.extensions import db
from app.models.job import Job
from app.models.invoice import Invoice
from app.models.vehicle import Vehicle
from app.utils.decorators import handle_database_errors, log_function_call, validate_pagination
from app.utils.roles import can_access_admin_portal
from app.utils.validators import sanitize_input, validate_positive_integer, validate_service_data, validate_part_data

# Create blueprint
administrator_bp = Blueprint('administrator', __name__)
logger = logging.getLogger(__name__)

# Initialize services
customer_service = CustomerService()
job_service = JobService()
billing_service = BillingService()


def require_admin_login():
    """Check administrator login status"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('auth.login'))

    if not can_access_admin_portal():
        flash('Administrator privileges required', 'error')
        return redirect(url_for('main.index'))

    return None


def _tenant_id():
    """Resolve the active tenant for the current admin session."""
    return session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)


def _tenant_jobs():
    """Return all jobs visible in the current tenant."""
    return Job.get_all_with_customer_info()


def _month_start_from_value(value):
    """Coerce a date, datetime, or ISO string to the first day of that month."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            return date(parsed.year, parsed.month, 1)
        except Exception:
            try:
                parsed_date = date.fromisoformat(raw[:10])
                return date(parsed_date.year, parsed_date.month, 1)
            except Exception:
                return None
    return None


def _job_month(job):
    """Return the activity month for a job record."""
    return _month_start_from_value(getattr(job, 'job_date', None)) or _month_start_from_value(getattr(job, 'created_at', None))


def _invoice_month(invoice):
    """Return the activity month for an invoice record with legacy fallbacks."""
    job = getattr(invoice, 'job', None)
    return (
        _month_start_from_value(getattr(invoice, 'issued_at', None))
        or _month_start_from_value(getattr(invoice, 'paid_at', None))
        or _month_start_from_value(getattr(invoice, 'due_date', None))
        or _month_start_from_value(getattr(job, 'job_date', None))
        or _month_start_from_value(getattr(job, 'created_at', None))
    )


def _status_summary(jobs):
    """Summarise job workflow states."""
    summary = {status: 0 for status in Job.VALID_STATUSES}
    for job in jobs or []:
        summary[job.status] = summary.get(job.status, 0) + 1
    return summary


def _add_months(month_start, months):
    """Return the first day of the month offset by a number of months."""
    month_index = month_start.month - 1 + months
    year = month_start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _month_range(start_month, end_month):
    """Yield first-of-month dates from start_month through end_month inclusive."""
    cursor = date(start_month.year, start_month.month, 1)
    end = date(end_month.year, end_month.month, 1)
    while cursor <= end:
        yield cursor
        cursor = _add_months(cursor, 1)


def _format_month_label(month_start):
    return month_start.strftime('%b %Y')


def _aggregate_reports_payload(months, jobs, invoices, customers, *, today=None):
    """Aggregate chart and ranking payloads for a supplied month range."""
    today = today or date.today()

    revenue_invoiced = defaultdict(float)
    revenue_collected = defaultdict(float)
    jobs_created = defaultdict(int)
    jobs_completed = defaultdict(int)
    customer_spend = defaultdict(float)
    customer_invoice_count = defaultdict(int)

    aging_bucket_labels = ['Current', '1-30 Days', '31-60 Days', '61-90 Days', '90+ Days']
    aging_buckets = {label: {'count': 0, 'amount': 0.0} for label in aging_bucket_labels}

    job_status_counts = {status: 0 for status in Job.VALID_STATUSES}
    invoice_status_counts = {
        Invoice.STATUS_DRAFT: 0,
        Invoice.STATUS_SENT: 0,
        Invoice.STATUS_PAID: 0,
        Invoice.STATUS_VOID: 0,
    }

    for job in jobs or []:
        job_status_counts[job.status] = job_status_counts.get(job.status, 0) + 1
        job_month = _job_month(job)
        if job_month and job_month in months:
            jobs_created[job_month] += 1
            if job.completed or job.status in {Job.STATUS_COMPLETED, Job.STATUS_DELIVERED}:
                jobs_completed[job_month] += 1

    for invoice in invoices or []:
        total_amount = float(invoice.total_amount or 0)
        invoice_status_counts[invoice.status] = invoice_status_counts.get(invoice.status, 0) + 1

        invoice_month = _invoice_month(invoice)
        if invoice_month and invoice_month in months:
            revenue_invoiced[invoice_month] += total_amount

        if invoice.status == Invoice.STATUS_PAID:
            collection_month = (
                _month_start_from_value(getattr(invoice, 'paid_at', None))
                or invoice_month
            )
            if collection_month and collection_month in months:
                revenue_collected[collection_month] += total_amount

        if invoice.customer_id:
            customer_spend[int(invoice.customer_id)] += total_amount
            customer_invoice_count[int(invoice.customer_id)] += 1

        if invoice.status != Invoice.STATUS_PAID:
            if invoice.due_date:
                days_overdue = (today - invoice.due_date).days
            else:
                days_overdue = 0

            if days_overdue <= 0:
                bucket = 'Current'
            elif days_overdue <= 30:
                bucket = '1-30 Days'
            elif days_overdue <= 60:
                bucket = '31-60 Days'
            elif days_overdue <= 90:
                bucket = '61-90 Days'
            else:
                bucket = '90+ Days'

            aging_buckets[bucket]['count'] += 1
            aging_buckets[bucket]['amount'] += total_amount

    top_customers = []
    customer_map = {getattr(c, 'customer_id', None): c for c in customers or []}
    for customer_id, amount in sorted(customer_spend.items(), key=lambda item: item[1], reverse=True)[:5]:
        customer = customer_map.get(customer_id)
        if customer:
            customer_name = getattr(customer, 'full_name', None) or f'Customer #{customer_id}'
        else:
            customer_name = f'Customer #{customer_id}'
        top_customers.append({
            'customer_id': customer_id,
            'customer_name': customer_name,
            'amount': round(float(amount), 2),
            'invoice_count': customer_invoice_count.get(customer_id, 0),
        })

    monthly_revenue = [round(revenue_invoiced.get(month, 0.0), 2) for month in months]
    monthly_collected = [round(revenue_collected.get(month, 0.0), 2) for month in months]
    monthly_jobs_created = [jobs_created.get(month, 0) for month in months]
    monthly_jobs_completed = [jobs_completed.get(month, 0) for month in months]

    has_series_activity = any(monthly_revenue) or any(monthly_collected) or any(monthly_jobs_created) or any(monthly_jobs_completed)

    return {
        'months': [_format_month_label(m) for m in months],
        'monthly_revenue': monthly_revenue,
        'monthly_collected': monthly_collected,
        'monthly_jobs_created': monthly_jobs_created,
        'monthly_jobs_completed': monthly_jobs_completed,
        'job_status_counts': [job_status_counts.get(status, 0) for status in Job.VALID_STATUSES],
        'job_status_labels': [Job.STATUS_LABELS.get(status, status.replace('_', ' ').title()) for status in Job.VALID_STATUSES],
        'invoice_status_counts': [
            invoice_status_counts.get(Invoice.STATUS_DRAFT, 0),
            invoice_status_counts.get(Invoice.STATUS_SENT, 0),
            invoice_status_counts.get(Invoice.STATUS_PAID, 0),
            invoice_status_counts.get(Invoice.STATUS_VOID, 0),
        ],
        'invoice_status_labels': ['Draft', 'Sent', 'Paid', 'Void'],
        'aging_bucket_labels': aging_bucket_labels,
        'aging_bucket_counts': [aging_buckets[label]['count'] for label in aging_bucket_labels],
        'aging_bucket_amounts': [round(aging_buckets[label]['amount'], 2) for label in aging_bucket_labels],
        'top_customers': top_customers,
        'has_series_activity': has_series_activity,
    }


def _build_reports_analytics(jobs, invoices, customers, period='6m'):
    """Build chart-ready analytics payloads for the tenant analytics page."""
    today = date.today()
    current_month = date(today.year, today.month, 1)
    period = (period or '6m').lower()

    all_time_candidates = []
    for job in jobs or []:
        job_month = _job_month(job)
        if job_month:
            all_time_candidates.append(job_month)
    for invoice in invoices or []:
        invoice_month = _invoice_month(invoice)
        if invoice_month:
            all_time_candidates.append(invoice_month)

    latest_relevant_month = max(all_time_candidates) if all_time_candidates else current_month
    chart_end_month = max(current_month, latest_relevant_month)

    if period == '12m':
        start_month = _add_months(current_month, -11)
        period_label = 'Last 12 months'
    elif period == 'ytd':
        start_month = date(today.year, 1, 1)
        period_label = 'Year to date'
    elif period == 'all':
        start_month = min(all_time_candidates) if all_time_candidates else current_month
        period_label = 'All time'
    else:
        start_month = _add_months(current_month, -5)
        period_label = 'Last 6 months'

    selected_months = list(_month_range(start_month, current_month))
    all_time_start = min(all_time_candidates) if all_time_candidates else current_month
    all_time_months = list(_month_range(all_time_start, chart_end_month))

    selected = _aggregate_reports_payload(selected_months, jobs, invoices, customers, today=today)
    all_time = _aggregate_reports_payload(all_time_months, jobs, invoices, customers, today=today)

    chart_payload = selected
    chart_period_label = period_label
    chart_source_note = None

    selected_total_points = sum(selected['monthly_revenue']) + sum(selected['monthly_collected']) + sum(selected['monthly_jobs_created']) + sum(selected['monthly_jobs_completed'])
    all_time_total_points = sum(all_time['monthly_revenue']) + sum(all_time['monthly_collected']) + sum(all_time['monthly_jobs_created']) + sum(all_time['monthly_jobs_completed'])

    if (
        (not selected['has_series_activity'] and all_time['has_series_activity'])
        or (selected_total_points == 0 and all_time_total_points > 0)
    ):
        chart_payload = all_time
        chart_period_label = f'{period_label} (showing full date range)'
        chart_source_note = 'No activity in selected period'

    selected_job_points = sum(selected['monthly_jobs_created']) + sum(selected['monthly_jobs_completed'])
    all_time_job_points = sum(all_time['monthly_jobs_created']) + sum(all_time['monthly_jobs_completed'])
    job_volume_payload = selected
    job_volume_period_label = period_label
    job_volume_source_note = None
    if (selected_job_points <= 1 and all_time_job_points > selected_job_points) or (not selected['monthly_jobs_created'] and all_time['monthly_jobs_created']):
        job_volume_payload = all_time
        job_volume_period_label = f'{period_label} (showing full date range)'
        job_volume_source_note = 'Sparse job volume in selected period'

    return {
        'period': period,
        'period_label': period_label,
        'chart_period_label': chart_period_label,
        'chart_source_note': chart_source_note,
        'start_month': start_month.strftime('%Y-%m-%d'),
        'end_month': chart_end_month.strftime('%Y-%m-%d'),
        'months': chart_payload['months'],
        'monthly_revenue': chart_payload['monthly_revenue'],
        'monthly_collected': chart_payload['monthly_collected'],
        'monthly_jobs_created': chart_payload['monthly_jobs_created'],
        'monthly_jobs_completed': chart_payload['monthly_jobs_completed'],
        'job_status_counts': chart_payload['job_status_counts'],
        'job_status_labels': chart_payload['job_status_labels'],
        'invoice_status_counts': chart_payload['invoice_status_counts'],
        'invoice_status_labels': chart_payload['invoice_status_labels'],
        'aging_bucket_labels': chart_payload['aging_bucket_labels'],
        'aging_bucket_counts': chart_payload['aging_bucket_counts'],
        'aging_bucket_amounts': chart_payload['aging_bucket_amounts'],
        'top_customers': chart_payload['top_customers'] if chart_payload['top_customers'] else all_time['top_customers'],
        'has_chart_activity': chart_payload['has_series_activity'] or all_time['has_series_activity'],
        'job_volume_months': job_volume_payload['months'],
        'job_volume_monthly_jobs_created': job_volume_payload['monthly_jobs_created'],
        'job_volume_monthly_jobs_completed': job_volume_payload['monthly_jobs_completed'],
        'job_volume_period_label': job_volume_period_label,
        'job_volume_source_note': job_volume_source_note,
    }


def _build_analytics_csv(report_data):
    """Return a CSV download for the analytics page."""
    analytics = report_data.get('analytics', {}) or {}
    job_stats = report_data.get('job_stats', {}) or {}
    billing_stats = report_data.get('billing_stats', {}) or {}
    customer_stats = report_data.get('customer_stats', {}) or {}
    period_info = report_data.get('period_info', {}) or {}

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['KarFix Analytics Export'])
    writer.writerow(['Period', report_data.get('selected_period', '6m')])
    writer.writerow(['Range start', period_info.get('start_date', '')])
    writer.writerow(['Range end', period_info.get('end_date', '')])
    writer.writerow([])
    writer.writerow(['Summary'])
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Revenue', float(billing_stats.get('total_revenue', 0) or 0)])
    writer.writerow(['Jobs', int(job_stats.get('total_jobs', 0) or 0)])
    writer.writerow(['Customers', int(customer_stats.get('total_customers', 0) or 0)])
    writer.writerow(['Overdue amount', float(billing_stats.get('total_overdue', 0) or 0)])
    writer.writerow(['Completion rate', float(job_stats.get('completion_rate', 0) or 0)])
    writer.writerow(['Payment rate', float(customer_stats.get('customer_payment_rate', 0) or 0)])
    writer.writerow([])
    writer.writerow(['Monthly trend'])
    writer.writerow(['Month', 'Invoiced', 'Collected', 'Jobs created', 'Jobs completed'])
    months = analytics.get('months', []) or []
    revenue = analytics.get('monthly_revenue', []) or []
    collected = analytics.get('monthly_collected', []) or []
    created = analytics.get('monthly_jobs_created', []) or []
    completed = analytics.get('monthly_jobs_completed', []) or []
    for idx, month in enumerate(months):
        writer.writerow([
            month,
            float(revenue[idx]) if idx < len(revenue) else 0,
            float(collected[idx]) if idx < len(collected) else 0,
            int(created[idx]) if idx < len(created) else 0,
            int(completed[idx]) if idx < len(completed) else 0,
        ])
    writer.writerow([])
    writer.writerow(['Job status'])
    writer.writerow(['Status', 'Count'])
    for idx, label in enumerate(analytics.get('job_status_labels', []) or []):
        counts = analytics.get('job_status_counts', []) or []
        writer.writerow([label, int(counts[idx]) if idx < len(counts) else 0])
    writer.writerow([])
    writer.writerow(['Invoice aging'])
    writer.writerow(['Bucket', 'Count', 'Amount'])
    aging_labels = analytics.get('aging_bucket_labels', []) or []
    aging_counts = analytics.get('aging_bucket_counts', []) or []
    aging_amounts = analytics.get('aging_bucket_amounts', []) or []
    for idx, label in enumerate(aging_labels):
        writer.writerow([
            label,
            int(aging_counts[idx]) if idx < len(aging_counts) else 0,
            float(aging_amounts[idx]) if idx < len(aging_amounts) else 0,
        ])
    writer.writerow([])
    writer.writerow(['Top customers'])
    writer.writerow(['Customer', 'Invoices', 'Amount'])
    for item in analytics.get('top_customers', []) or []:
        writer.writerow([
            item.get('customer_name', ''),
            int(item.get('invoice_count', 0) or 0),
            float(item.get('amount', 0) or 0),
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = (
        f"attachment; filename=karfix_analytics_{report_data.get('selected_period', '6m')}_{date.today().isoformat()}.csv"
    )
    return response

def _tenant_vehicle_rows(customers):
    """Flatten customer vehicles into a list of dashboard rows."""
    rows = []
    for customer in customers or []:
        preferred_name = customer.preferred_tenant.name if getattr(customer, 'preferred_tenant', None) else None
        for vehicle in customer.get_vehicles():
            vehicle_history = vehicle.get_jobs()
            rows.append({
                'vehicle': vehicle,
                'customer': customer,
                'customer_name': customer.full_name,
                'preferred_workshop_name': preferred_name,
                'history_count': len(vehicle_history),
                'last_service_date': vehicle_history[0].job_date if vehicle_history else None,
                'last_service_job': vehicle_history[0] if vehicle_history else None,
            })
    rows.sort(key=lambda row: (
        row['customer_name'] or '',
        row['vehicle'].display_name if row['vehicle'] else ''
    ))
    return rows


def _annotate_customers(customers):
    """Attach summary counts to customer list records."""
    annotated = []
    for customer in customers or []:
        data = customer.to_dict()
        jobs = customer.get_jobs()
        vehicles = customer.get_vehicles()
        data['job_count'] = len(jobs)
        data['vehicle_count'] = len(vehicles)
        data['active_jobs'] = len([job for job in jobs if not job.completed])
        data['preferred_workshop_name'] = customer.preferred_tenant.name if getattr(customer, 'preferred_tenant', None) else None
        portal_status = customer_service.get_portal_access_status(customer.customer_id)
        data['has_portal_access'] = bool(portal_status.get('enabled'))
        data['portal_status'] = portal_status
        annotated.append(data)
    return annotated


@administrator_bp.route('/dashboard')
@handle_database_errors
@log_function_call
def dashboard():
    """Workshop dashboard for tenant administrators."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()

        customers = customer_service.get_all_customers()
        customer_rows = _annotate_customers(customers)
        jobs = _tenant_jobs()
        active_jobs = [job for job in jobs if not job.completed]
        bookings = [job for job in jobs if job.status == Job.STATUS_DRAFT or (job.job_date >= date.today() and not job.completed)]
        vehicles = _tenant_vehicle_rows(customers)
        status_summary = _status_summary(jobs)

        return render_template(
            'administrator/dashboard.html',
            job_stats=job_stats,
            billing_stats=billing_stats,
            total_customers=len(customers),
            total_vehicles=len(vehicles),
            active_jobs_count=len(active_jobs),
            booking_count=len(bookings),
            status_summary=status_summary,
            bookings=bookings[:5],
            active_jobs=active_jobs[:8],
            recent_jobs=jobs[:8],
            vehicle_rows=vehicles[:8],
            customer_rows=customer_rows[:8],
            customers_with_unpaid=len(customer_service.get_customers_with_filter(has_unpaid=True)),
            customers_with_overdue=len(customer_service.get_customers_with_filter(has_overdue=True)),
            overdue_bills=billing_service.get_overdue_bills()[:5],
            current_date=date.today(),
        )

    except Exception as e:
        logger.error(f"Administrator dashboard loading failed: {e}")
        flash('Failed to load dashboard', 'error')
        return render_template(
            'administrator/dashboard.html',
            job_stats={},
            billing_stats={},
            total_customers=0,
            total_vehicles=0,
            active_jobs_count=0,
            booking_count=0,
            status_summary={status: 0 for status in Job.VALID_STATUSES},
            bookings=[],
            active_jobs=[],
            recent_jobs=[],
            vehicle_rows=[],
            customer_rows=[],
            customers_with_unpaid=0,
            customers_with_overdue=0,
            overdue_bills=[],
            current_date=date.today(),
        )


@administrator_bp.route('/customers')
@validate_pagination
@handle_database_errors
@log_function_call
def customer_list(page=1, per_page=20):
    """Customer management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        filter_type = sanitize_input(request.args.get('filter', 'all'))
        search_query = sanitize_input(request.args.get('search', ''))

        if filter_type == 'unpaid':
            customers = customer_service.get_customers_with_filter(has_unpaid=True)
        elif filter_type == 'overdue':
            customers = customer_service.get_customers_with_filter(has_overdue=True)
        elif search_query:
            customers = customer_service.search_customers(search_query)
        else:
            customers = customer_service.get_all_customers()

        customers_page = _annotate_customers(customers)
        total = len(customers_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        customers_page = customers_page[start_idx:end_idx]
        total_pages = (total + per_page - 1) // per_page
        all_jobs = _tenant_jobs()
        all_bookings = [job for job in all_jobs if job.status == Job.STATUS_DRAFT or (job.job_date >= date.today() and not job.completed)]
        all_vehicles = _tenant_vehicle_rows(customers)

        return render_template(
            'administrator/customer_list.html',
            customers=customers_page,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            filter_type=filter_type,
            search_query=search_query,
            vehicle_total=len(all_vehicles),
            active_job_total=len([job for job in all_jobs if not job.completed]),
            booking_total=len(all_bookings),
        )

    except Exception as e:
        logger.error(f"Customer management page loading failed: {e}")
        flash('Failed to load customer list', 'error')
        return render_template(
            'administrator/customer_list.html',
            customers=[],
            page=1,
            per_page=per_page,
            total=0,
            total_pages=0,
            filter_type='all',
            search_query='',
            vehicle_total=0,
            active_job_total=0,
            booking_total=0,
        )


@administrator_bp.route('/billing')
@handle_database_errors
@log_function_call
def billing_management():
    """Billing management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        tenant_id = _tenant_id()
        filter_type = sanitize_input(request.args.get('filter', 'unpaid'))
        customer_name = sanitize_input(request.args.get('customer', ''))
        invoice_service = InvoiceService()

        if filter_type == 'overdue':
            invoices = invoice_service.get_overdue_invoices(customer_name if customer_name != 'Choose...' else None)
        elif filter_type == 'all':
            invoices = invoice_service.get_invoices_for_tenant(tenant_id)
            if customer_name and customer_name != 'Choose...':
                target = customer_name.strip().lower()
                invoices = [inv for inv in invoices if (inv.customer_name_display or '').strip().lower() == target]
        else:
            invoices = invoice_service.get_unpaid_invoices(customer_name if customer_name != 'Choose...' else None)

        customers = customer_service.get_all_customers()
        customer_names = sorted({f"{c.first_name} {c.family_name}".strip() for c in customers})
        billing_stats = invoice_service.get_invoice_billing_statistics(tenant_id)

        return render_template(
            'administrator/billing.html',
            invoices=invoices,
            bills=invoices,
            filter_type=filter_type,
            customer_name=customer_name,
            customer_names=customer_names,
            billing_stats=billing_stats,
        )

    except Exception as e:
        logger.error(f"Billing management page loading failed: {e}")
        flash('Failed to load billing management page', 'error')
        return render_template(
            'administrator/billing.html',
            invoices=[],
            bills=[],
            filter_type='unpaid',
            customer_name='',
            customer_names=[],
            billing_stats=InvoiceService().get_invoice_billing_statistics(_tenant_id()),
        )


@administrator_bp.route('/overdue-bills', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def overdue_bills():
    """Overdue bills page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        # Get overdue days threshold
        days_threshold = request.values.get('days', 14, type=int)
        if days_threshold < 1:
            days_threshold = 14

        selected_customer_id = request.values.get('customer_id', type=int)

        # Get overdue bills
        overdue_bills_list = billing_service.get_overdue_bills(days_threshold)

        # Calculate total amount
        total_overdue_amount = sum(float(bill.total_cost or 0) for bill in overdue_bills_list)

        selected_customer = None
        if selected_customer_id:
            selected_customer = customer_service.get_customer_by_id(selected_customer_id)

        customers = customer_service.get_all_customers()

        return render_template('administrator/overdue_bills.html',
                             jobs=overdue_bills_list,
                             overdue_bills=overdue_bills_list,
                             total_overdue_amount=total_overdue_amount,
                             days_threshold=days_threshold,
                             total_count=len(overdue_bills_list),
                             selected_customer=selected_customer,
                             customers=customers)

    except Exception as e:
        logger.error(f"Overdue bills page loading failed: {e}")
        flash('Failed to load overdue bills page', 'error')
        return render_template('administrator/overdue_bills.html',
                             jobs=[],
                             overdue_bills=[],
                             total_overdue_amount=0,
                             days_threshold=14,
                             total_count=0,
                             selected_customer=None,
                             customers=[])


@administrator_bp.route('/pay-bills')
@handle_database_errors
@log_function_call
def pay_bills():
    """Compatibility redirect to the invoice-based billing dashboard."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    customer_name = sanitize_input(request.args.get('customer', ''))
    filter_type = sanitize_input(request.args.get('filter', 'unpaid')) or 'unpaid'
    params = {'filter': filter_type}
    if customer_name:
        params['customer'] = customer_name
    return redirect(url_for('administrator.billing_management', **params))


@administrator_bp.route('/customers/<int:customer_id>/pay', methods=['POST'])
@handle_database_errors
def pay_customer_bills(customer_id):
    """Mark all bills for a customer as paid"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        success, errors, count = billing_service.mark_customer_bills_as_paid(customer_id)

        if success:
            flash(f'Successfully marked {count} bills as paid!', 'success')
        else:
            for error in errors:
                flash(error, 'error')

        return redirect(url_for('administrator.customer_list'))

    except Exception as e:
        logger.error(f"Failed to mark customer bills as paid: {e}")
        flash('Failed to update payment status, please try again later', 'error')
        return redirect(url_for('administrator.customer_list'))


@administrator_bp.route('/jobs/invoice/send', methods=['POST'])
@handle_database_errors
def send_selected_job_invoice():
    """Generate and email an invoice for the selected completed job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    job_id = request.form.get('job_id', type=int)
    if not job_id:
        flash('Please select a completed bill to email an invoice', 'warning')
        return redirect(url_for('administrator.pay_bills'))

    try:
        ok, errors, invoice = InvoiceService().create_invoice_for_job(job_id, send_email=False)
        if ok and invoice:
            flash(f'Invoice {invoice.invoice_number} is available in the customer portal.', 'success')
        else:
            for error in errors:
                flash(error, 'error')
    except Exception as e:
        logger.error(f'Failed to send invoice for job {job_id}: {e}')
        flash('Failed to generate invoice, please try again later', 'error')

    return_page = sanitize_input(request.form.get('return_page', 'pay_bills'))
    if return_page == 'overdue_bills':
        return redirect(url_for('administrator.overdue_bills'))
    if return_page == 'billing':
        return redirect(url_for('administrator.billing_management'))
    return redirect(url_for('administrator.pay_bills'))


@administrator_bp.route('/jobs/<int:job_id>/invoice/send', methods=['POST'])
@handle_database_errors
def send_job_invoice(job_id):
    """Generate and email an invoice for a completed job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        ok, errors, invoice = InvoiceService().create_invoice_for_job(job_id, send_email=False)
        if ok and invoice:
            flash(f'Invoice {invoice.invoice_number} is available in the customer portal.', 'success')
        else:
            for error in errors:
                flash(error, 'error')
    except Exception as e:
        logger.error(f'Failed to send invoice for job {job_id}: {e}')
        flash('Failed to generate invoice, please try again later', 'error')

    return_page = sanitize_input(request.form.get('return_page', 'pay_bills'))
    if return_page == 'overdue_bills':
        return redirect(url_for('administrator.overdue_bills'))
    if return_page == 'billing':
        return redirect(url_for('administrator.billing_management'))
    return redirect(url_for('administrator.pay_bills'))


@administrator_bp.route('/jobs/<int:job_id>/pay', methods=['POST'])
@handle_database_errors
def pay_single_bill(job_id):
    """Mark single work order as paid"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        success, errors = billing_service.mark_job_as_paid(job_id)

        if success:
            flash('Bill payment status updated!', 'success')
        else:
            for error in errors:
                flash(error, 'error')

        # Redirect based on source page
        return_page = sanitize_input(request.form.get('return_page', 'pay_bills'))
        if return_page == 'overdue_bills':
            return redirect(url_for('administrator.overdue_bills'))
        else:
            return redirect(url_for('administrator.pay_bills'))

    except Exception as e:
        logger.error(f"Failed to mark bill as paid: {e}")
        flash('Failed to update payment status, please try again later', 'error')
        return redirect(url_for('administrator.pay_bills'))


@administrator_bp.route('/mark-paid', methods=['POST'])
@handle_database_errors
def mark_paid():
    """Mark a bill as paid from billing views"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    invoice_id = request.form.get('invoice_id', type=int)
    job_id = request.form.get('job_id', type=int) or request.form.get('bill_select', type=int)
    invoice_service = InvoiceService()

    try:
        if invoice_id:
            success, errors, invoice = invoice_service.mark_invoice_paid(invoice_id)
            if success:
                flash(f'Invoice {invoice.invoice_number} payment status updated!', 'success')
            else:
                for error in errors:
                    flash(error, 'error')
        elif job_id:
            invoice = invoice_service.get_invoice_for_job(job_id)
            if not invoice:
                ok, errors, invoice = invoice_service.create_invoice_for_job(job_id, send_email=False)
                if not ok or not invoice:
                    for error in errors:
                        flash(error, 'error')
                    return redirect(url_for('administrator.billing_management'))
            success, errors, invoice = invoice_service.mark_invoice_paid(invoice.invoice_id)
            if success:
                flash('Bill payment status updated!', 'success')
            else:
                for error in errors:
                    flash(error, 'error')
        else:
            flash('Please select a completed bill to update', 'warning')

        return_page = sanitize_input(request.form.get('return_page', 'billing'))
        if return_page == 'overdue_bills':
            return redirect(url_for('administrator.overdue_bills'))
        if return_page == 'pay_bills':
            return redirect(url_for('administrator.billing_management', filter='unpaid'))
        return redirect(url_for('administrator.billing_management'))
    except Exception as e:
        logger.error(f"Failed to mark bill as paid: {e}")
        flash('Failed to update payment status, please try again later', 'error')
        return redirect(url_for('administrator.billing_management'))


@administrator_bp.route('/analytics')
@handle_database_errors
@log_function_call
def analytics():
    """Analytics page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        tenant_id = _tenant_id()
        period = sanitize_input(request.args.get('period', '6m'))

        customers = customer_service.get_all_customers()
        jobs = _tenant_jobs()
        invoices = InvoiceService().get_invoices_for_tenant(tenant_id)
        status_summary = _status_summary(jobs)
        billing_stats = billing_service.get_billing_statistics()

        # Job metrics remain job-driven. Billing metrics stay invoice-driven.
        total_jobs = len(jobs)
        completed_jobs = status_summary.get(Job.STATUS_COMPLETED, 0) + status_summary.get(Job.STATUS_DELIVERED, 0)
        in_progress_jobs = status_summary.get(Job.STATUS_IN_PROGRESS, 0) + status_summary.get(Job.STATUS_AWAITING_PARTS, 0)
        pending_jobs = status_summary.get(Job.STATUS_DRAFT, 0)
        overdue_jobs = len([job for job in jobs if not job.completed and getattr(job, 'job_date', None) and job.job_date < date.today()])

        job_stats = {
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'in_progress_jobs': in_progress_jobs,
            'pending_jobs': pending_jobs,
            'open_jobs': in_progress_jobs + pending_jobs,
            'overdue_jobs': overdue_jobs,
            'completion_rate': (completed_jobs / total_jobs * 100) if total_jobs > 0 else 0,
        }

        total_customers = len(customers)
        customers_with_unpaid = customer_service.get_customers_with_filter(has_unpaid=True)
        customers_with_overdue = customer_service.get_customers_with_filter(has_overdue=True)
        customer_payment_rate = ((total_customers - len(customers_with_unpaid)) / total_customers * 100) if total_customers > 0 else 0
        customer_stats = {
            'total_customers': total_customers,
            'customers_with_unpaid': len(customers_with_unpaid),
            'customers_with_overdue': len(customers_with_overdue),
            'customer_payment_rate': customer_payment_rate,
            'total': total_customers,
            'active': total_customers,
            'with_unpaid': len(customers_with_unpaid),
            'with_overdue': len(customers_with_overdue),
        }

        analytics = _build_reports_analytics(jobs, invoices, customers, period=period)

        report_data = {
            'job_stats': job_stats,
            'billing_stats': billing_stats,
            'customer_stats': customer_stats,
            'period_info': {
                'start_date': analytics['start_month'],
                'end_date': analytics['end_month'],
                'comparison': analytics['period_label'],
                'current_month': date.today().replace(day=1).strftime('%B %Y'),
                'last_month': (date.today().replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%B %Y'),
                'generated_date': date.today().strftime('%Y-%m-%d')
            },
            'analytics': analytics,
            'selected_period': period,
        }

        export_format = sanitize_input(request.args.get('export', '')).lower()
        if export_format == 'csv':
            return _build_analytics_csv(report_data)

        return render_template('administrator/reports.html',
                             report_data=report_data)

    except Exception as e:
        logger.error(f"Analytics page loading failed: {e}")
        flash('Failed to load analytics', 'error')
        empty_analytics = _build_reports_analytics([], [], [], period='6m')
        return render_template('administrator/reports.html',
                             report_data={
                                 'job_stats': {},
                                 'billing_stats': {},
                                 'customer_stats': {},
                                 'period_info': {
                                     'start_date': date.today().strftime('%Y-%m-%d'),
                                     'end_date': date.today().strftime('%Y-%m-%d'),
                                     'comparison': 'Last 6 months',
                                     'current_month': date.today().replace(day=1).strftime('%B %Y'),
                                     'last_month': (date.today().replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%B %Y'),
                                     'generated_date': date.today().strftime('%Y-%m-%d')
                                 },
                                 'analytics': empty_analytics,
                                 'selected_period': '6m',
                             })




@administrator_bp.route('/reports')
def reports():
    """Backward-compatible redirect to analytics."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response
    return redirect(url_for('administrator.analytics', **request.args.to_dict(flat=True)))

@administrator_bp.route('/invoices')
@handle_database_errors
@log_function_call
def invoice_dashboard():
    """Tenant invoice dashboard."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        tenant_id = _tenant_id()
        invoices = InvoiceService().get_invoices_for_tenant(tenant_id)
        summary = {
            'total': len(invoices),
            'draft': len([i for i in invoices if i.status == i.STATUS_DRAFT]),
            'sent': len([i for i in invoices if i.status == i.STATUS_SENT]),
            'paid': len([i for i in invoices if i.status == i.STATUS_PAID]),
            'overdue': len([i for i in invoices if i.is_overdue]),
            'outstanding': sum(float(i.total_amount or 0) for i in invoices if i.status != i.STATUS_PAID),
        }
        return render_template('administrator/invoices.html', invoices=invoices, summary=summary)
    except Exception as e:
        logger.error(f'Invoice dashboard loading failed: {e}')
        flash('Failed to load invoice dashboard', 'error')
        return render_template('administrator/invoices.html', invoices=[], summary={'total': 0, 'draft': 0, 'sent': 0, 'paid': 0, 'overdue': 0, 'outstanding': 0})


@administrator_bp.route('/invoices/<int:invoice_id>')
@handle_database_errors
@log_function_call
def invoice_detail(invoice_id: int):
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response
    invoice = InvoiceService().get_invoice_by_id(invoice_id)
    if not invoice or invoice.tenant_id != _tenant_id():
        flash('Invoice not found.', 'error')
        return redirect(url_for('administrator.invoice_dashboard'))
    return render_template('administrator/invoice_detail.html', invoice=invoice)


@administrator_bp.route('/invoices/<int:invoice_id>/download')
@handle_database_errors
@log_function_call
def invoice_download(invoice_id: int):
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response
    invoice = InvoiceService().get_invoice_by_id(invoice_id)
    if not invoice or invoice.tenant_id != _tenant_id():
        flash('Invoice not found.', 'error')
        return redirect(url_for('administrator.invoice_dashboard'))
    response = make_response(render_template('administrator/invoice_download.html', invoice=invoice))
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename={invoice.invoice_number}.html'
    return response


@administrator_bp.route('/api/customers/<int:customer_id>/billing-summary')
@handle_database_errors
def api_customer_billing_summary(customer_id):
    """API: Get customer billing summary"""
    try:
        summary = billing_service.get_customer_billing_summary(customer_id)
        return jsonify(summary)

    except Exception as e:
        logger.error(f"Failed to get customer billing summary: {e}")
        return jsonify({'error': 'Failed to get billing summary'}), 500


@administrator_bp.route('/api/billing/statistics')
@handle_database_errors
def api_billing_statistics():
    """API: Get billing statistics"""
    try:
        stats = billing_service.get_billing_statistics()
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get billing statistics: {e}")
        return jsonify({'error': 'Failed to get statistics'}), 500


@administrator_bp.route('/api/dashboard/summary')
@handle_database_errors
def api_dashboard_summary():
    """API: Get dashboard summary"""
    try:
        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()

        # Customer statistics
        total_customers = len(customer_service.get_all_customers())
        customers_with_unpaid = customer_service.get_customers_with_filter(has_unpaid=True)
        customers_with_overdue = customer_service.get_customers_with_filter(has_overdue=True)

        summary = {
            'jobs': job_stats,
            'billing': billing_stats,
            'customers': {
                'total': total_customers,
                'with_unpaid': len(customers_with_unpaid),
                'with_overdue': len(customers_with_overdue)
            },
            'alerts': {
                'overdue_bills': len(billing_service.get_overdue_bills()),
                'pending_jobs': job_stats.get('pending_jobs', 0)
            }
        }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Failed to get dashboard summary: {e}")
        return jsonify({'error': 'Failed to get summary'}), 500


@administrator_bp.route('/api/export/customers')
@handle_database_errors
def api_export_customers():
    """API: Export customer data"""
    try:
        customers = customer_service.get_all_customers()
        customer_data = []

        for c in customers:
            customer_info = c.to_dict()
            customer_info['total_unpaid'] = c.get_total_unpaid_amount()
            customer_info['has_overdue'] = c.has_overdue_bills()
            customer_data.append(customer_info)

        return jsonify({
            'data': customer_data,
            'export_date': date.today().isoformat(),
            'total_count': len(customer_data)
        })

    except Exception as e:
        logger.error(f"Failed to export customer data: {e}")
        return jsonify({'error': 'Failed to export data'}), 500


@administrator_bp.route('/api/customers/<int:customer_id>/summary')
@handle_database_errors
def api_customer_summary(customer_id):
    """API: Get customer summary"""
    try:
        customer = customer_service.get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        stats = customer_service.get_customer_statistics(customer_id)
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get customer summary: {e}")
        return jsonify({'error': 'Failed to get customer information'}), 500


# =============================================================================
# ORGANIZATION SETTINGS
# =============================================================================

@administrator_bp.route('/settings', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def org_settings():
    """Organization settings page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant import Tenant
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    tenant = Tenant.find_by_id(tenant_id)
    if not tenant:
        flash('Organization not found', 'error')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        try:
            tenant.name = sanitize_input(request.form.get('name', tenant.name))
            tenant.email = sanitize_input(request.form.get('email', '')) or tenant.email
            tenant.phone = sanitize_input(request.form.get('phone', '')) or tenant.phone
            tenant.address = sanitize_input(request.form.get('address', '')) or tenant.address

            settings = tenant.settings or {}
            tax_rate = request.form.get('tax_rate')
            if tax_rate:
                try:
                    settings['tax_rate'] = float(tax_rate)
                except ValueError:
                    pass
            currency = sanitize_input(request.form.get('currency', tenant.currency_code)) or tenant.currency_code
            currency = currency.upper()
            settings['currency'] = currency if currency in Tenant.VALID_CURRENCIES else tenant.currency_code
            tenant.settings = settings

            session['current_tenant_name'] = tenant.name
            db.session.commit()
            flash('Organization settings updated!', 'success')
        except Exception as e:
            logger.error(f"Failed to update org settings: {e}")
            db.session.rollback()
            flash('Failed to update settings', 'error')

    return render_template('administrator/org_settings.html', tenant=tenant, org=tenant)


# =============================================================================
# TEAM MANAGEMENT
# =============================================================================

@administrator_bp.route('/team')
@handle_database_errors
@log_function_call
def team_members():
    """Team member management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant_membership import TenantMembership
    from app.models.user import User
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        memberships = db.session.execute(
            db.select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id
            ).order_by(TenantMembership.role, TenantMembership.created_at)
        ).scalars().all()

        members = []
        for m in memberships:
            user = User.find_by_id(m.user_id)
            if user:
                members.append({
                    'membership_id': m.id,
                    'user_id': m.user_id,
                    'username': user.username,
                    'email': user.email,
                    'role': m.role,
                    'status': m.status,
                    'is_default': m.is_default,
                    'accepted_at': m.accepted_at,
                    'invited_at': m.invited_at,
                })

        return render_template('administrator/team_members.html',
                             members=members,
                             available_roles=TenantMembership.VALID_ROLES)

    except Exception as e:
        logger.error(f"Failed to load team members: {e}")
        flash('Failed to load team members', 'error')
        return render_template('administrator/team_members.html',
                             members=[],
                             available_roles=[])


@administrator_bp.route('/team/invite', methods=['POST'])
@handle_database_errors
def invite_team_member():
    """Invite a new team member"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.services.tenant_service import TenantService

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    email = sanitize_input(request.form.get('email', ''))
    role = sanitize_input(request.form.get('role', 'viewer'))
    user_id = session.get('user_id')

    if not email:
        flash('Email is required', 'error')
        return redirect(url_for('administrator.team_members'))

    tenant_service = TenantService()
    success, errors, membership = tenant_service.invite_member(
        tenant_id=tenant_id,
        email=email,
        role=role,
        invited_by_user_id=user_id,
    )

    if success:
        flash(f'Invitation sent to {email}!', 'success')
    else:
        for error in errors:
            flash(error, 'error')

    return redirect(url_for('administrator.team_members'))


@administrator_bp.route('/team/<int:membership_id>/role', methods=['POST'])
@handle_database_errors
def update_team_member_role(membership_id):
    """Update a team member role"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant_membership import TenantMembership
    from app.extensions import db

    new_role = sanitize_input(request.form.get('new_role', 'viewer'))
    if new_role not in TenantMembership.VALID_ROLES:
        flash('Invalid role selected', 'error')
        return redirect(url_for('administrator.team_members'))

    membership = db.session.get(TenantMembership, membership_id)
    if not membership:
        flash('Team member not found', 'error')
        return redirect(url_for('administrator.team_members'))

    membership.role = new_role
    db.session.commit()
    flash('Team member role updated!', 'success')
    return redirect(url_for('administrator.team_members'))


@administrator_bp.route('/team/<int:membership_id>/remove', methods=['POST'])
@handle_database_errors
def remove_team_member(membership_id):
    """Remove a team member"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant_membership import TenantMembership
    from app.extensions import db

    membership = db.session.get(TenantMembership, membership_id)
    if not membership:
        flash('Team member not found', 'error')
        return redirect(url_for('administrator.team_members'))

    db.session.delete(membership)
    db.session.commit()
    flash('Team member removed!', 'success')
    return redirect(url_for('administrator.team_members'))


# =============================================================================
# SERVICE CATALOG
# =============================================================================

@administrator_bp.route('/services', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def service_catalog():
    """Service catalog management"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.service import Service
    from app.extensions import db

    tenant_id = _tenant_id()
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    g.current_tenant_id = tenant_id

    if request.method == 'POST':
        action = sanitize_input(request.form.get('action', 'add')).strip().lower()
        service_id = request.form.get('service_id', type=int)
        service_name = sanitize_input(request.form.get('service_name', '')).strip()
        cost = request.form.get('cost')
        category = sanitize_input(request.form.get('category', 'General')).strip() or 'General'
        description = sanitize_input(request.form.get('description', '')).strip()
        estimated_duration = request.form.get('estimated_duration', type=int)
        if estimated_duration is None:
            estimated_duration = request.form.get('duration', type=int)

        if action in {'add', 'edit'}:
            validation = validate_service_data({
                'service_name': service_name,
                'cost': cost,
            })
            if not validation.is_valid:
                for error in validation.get_errors():
                    flash(error, 'error')
            else:
                try:
                    if action == 'add':
                        service = Service(
                            tenant_id=tenant_id,
                            service_name=service_name,
                            cost=float(cost),
                            category=category,
                            description=description,
                            estimated_duration_minutes=estimated_duration,
                            is_active=True,
                        )
                        db.session.add(service)
                        db.session.commit()
                        flash(f'Service "{service_name}" added!', 'success')
                    else:
                        if not service_id:
                            flash('Please select a service to edit', 'warning')
                        else:
                            service = Service.find_by_id(service_id)
                            if not service or service.tenant_id != tenant_id:
                                flash('Service not found', 'error')
                            else:
                                service.service_name = service_name
                                service.cost = float(cost)
                                service.category = category
                                service.description = description
                                service.estimated_duration_minutes = estimated_duration
                                db.session.commit()
                                flash(f'Service "{service_name}" updated!', 'success')
                except Exception as e:
                    logger.error(f"Failed to save service: {e}")
                    db.session.rollback()
                    flash('Failed to save service', 'error')

        elif action == 'toggle':
            if service_id:
                service = Service.find_by_id(service_id)
                if service and service.tenant_id == tenant_id:
                    service.is_active = not bool(service.is_active)
                    db.session.commit()
                    status = 'activated' if service.is_active else 'deactivated'
                    flash(f'Service {status}!', 'success')
                else:
                    flash('Service not found', 'error')

        return redirect(url_for('administrator.service_catalog'))

    # GET - load services
    try:
        services = Service.get_all_sorted()
        active = len([s for s in services if getattr(s, 'is_active', True)])
        inactive = len(services) - active
        avg_cost = round(sum(float(getattr(s, 'cost', 0) or 0) for s in services) / len(services), 2) if services else 0.0
        stats = {
            'total': len(services),
            'active': active,
            'inactive': inactive,
            'average_cost': avg_cost,
        }
        return render_template('administrator/service_catalog.html', services=services, stats=stats)
    except Exception as e:
        logger.error(f"Failed to load service catalog: {e}")
        flash('Failed to load service catalog', 'error')
        return render_template('administrator/service_catalog.html', services=[], stats={'total': 0, 'active': 0, 'inactive': 0, 'average_cost': 0.0})


# =============================================================================
# PARTS CATALOG
# =============================================================================

@administrator_bp.route('/parts', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def parts_catalog():
    """Parts catalog management"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.part import Part
    from app.extensions import db

    tenant_id = _tenant_id()
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    g.current_tenant_id = tenant_id

    if request.method == 'POST':
        action = sanitize_input(request.form.get('action', 'add')).strip().lower()
        part_id = request.form.get('part_id', type=int)
        part_name = sanitize_input(request.form.get('part_name', '')).strip()
        cost = request.form.get('cost')
        sku = sanitize_input(request.form.get('sku', '')).strip() or None
        category = sanitize_input(request.form.get('category', 'General')).strip() or 'General'
        description = sanitize_input(request.form.get('description', '')).strip()
        supplier = sanitize_input(request.form.get('supplier', '')).strip() or None

        if action in {'add', 'edit'}:
            validation = validate_part_data({
                'part_name': part_name,
                'cost': cost,
            })
            if not validation.is_valid:
                for error in validation.get_errors():
                    flash(error, 'error')
            else:
                try:
                    if action == 'add':
                        part = Part(
                            tenant_id=tenant_id,
                            part_name=part_name,
                            cost=float(cost),
                            sku=sku,
                            category=category,
                            description=description,
                            supplier=supplier,
                            is_active=True,
                        )
                        db.session.add(part)
                        db.session.commit()
                        flash(f'Part "{part_name}" added!', 'success')
                    else:
                        if not part_id:
                            flash('Please select a part to edit', 'warning')
                        else:
                            part = Part.find_by_id(part_id)
                            if not part or part.tenant_id != tenant_id:
                                flash('Part not found', 'error')
                            else:
                                part.part_name = part_name
                                part.cost = float(cost)
                                part.sku = sku
                                part.category = category
                                part.description = description
                                part.supplier = supplier
                                db.session.commit()
                                flash(f'Part "{part_name}" updated!', 'success')
                except Exception as e:
                    logger.error(f"Failed to save part: {e}")
                    db.session.rollback()
                    flash('Failed to save part', 'error')

        elif action == 'toggle':
            if part_id:
                part = Part.find_by_id(part_id)
                if part and part.tenant_id == tenant_id:
                    part.is_active = not bool(part.is_active)
                    db.session.commit()
                    status = 'activated' if part.is_active else 'deactivated'
                    flash(f'Part {status}!', 'success')
                else:
                    flash('Part not found', 'error')

        return redirect(url_for('administrator.parts_catalog'))

    # GET - load parts
    try:
        parts = Part.get_all_sorted()
        active = len([p for p in parts if getattr(p, 'is_active', True)])
        inactive = len(parts) - active
        avg_cost = round(sum(float(getattr(p, 'cost', 0) or 0) for p in parts) / len(parts), 2) if parts else 0.0
        stats = {
            'total': len(parts),
            'active': active,
            'inactive': inactive,
            'average_cost': avg_cost,
        }
        return render_template('administrator/parts_catalog.html', parts=parts, stats=stats)
    except Exception as e:
        logger.error(f"Failed to load parts catalog: {e}")
        flash('Failed to load parts catalog', 'error')
        return render_template('administrator/parts_catalog.html', parts=[], stats={'total': 0, 'active': 0, 'inactive': 0, 'average_cost': 0.0})


# =============================================================================
# INVENTORY MANAGEMENT
# =============================================================================

@administrator_bp.route('/inventory')
@handle_database_errors
@log_function_call
def inventory():
    """Inventory dashboard"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.inventory import Inventory, InventoryTransaction
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        inventory_items = db.session.execute(
            db.select(Inventory).where(Inventory.tenant_id == tenant_id)
        ).scalars().all()

        # Get recent transactions
        recent_transactions = db.session.execute(
            db.select(InventoryTransaction)
            .where(InventoryTransaction.tenant_id == tenant_id)
            .order_by(InventoryTransaction.created_at.desc())
            .limit(20)
        ).scalars().all()

        # Identify low stock items
        low_stock = [item for item in inventory_items
                     if item.quantity_on_hand <= item.reorder_level]

        return render_template('administrator/inventory.html',
                             inventory_items=inventory_items,
                             recent_transactions=recent_transactions,
                             low_stock=low_stock,
                             total_items=len(inventory_items))

    except Exception as e:
        logger.error(f"Failed to load inventory: {e}")
        flash('Failed to load inventory', 'error')
        return render_template('administrator/inventory.html',
                             inventory_items=[],
                             recent_transactions=[],
                             low_stock=[],
                             total_items=0)


@administrator_bp.route('/inventory/adjust', methods=['POST'])
@handle_database_errors
def inventory_adjust():
    """Adjust inventory stock level"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.inventory import Inventory, InventoryTransaction
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    inventory_id = request.form.get('inventory_id', type=int)
    adjustment = request.form.get('quantity', type=int)
    transaction_type = sanitize_input(request.form.get('transaction_type', 'adjustment'))
    notes = sanitize_input(request.form.get('notes', ''))

    if not inventory_id or adjustment is None:
        flash('Invalid adjustment data', 'error')
        return redirect(url_for('administrator.inventory'))

    try:
        item = db.session.get(Inventory, inventory_id)
        if not item or item.tenant_id != tenant_id:
            flash('Inventory item not found', 'error')
            return redirect(url_for('administrator.inventory'))

        # Update quantity
        item.quantity_on_hand += adjustment

        # Record transaction
        transaction = InventoryTransaction(
            tenant_id=tenant_id,
            inventory_id=inventory_id,
            transaction_type=transaction_type,
            quantity=adjustment,
            performed_by=session.get('user_id'),
            notes=notes,
        )
        db.session.add(transaction)
        db.session.commit()

        flash(f'Inventory adjusted by {adjustment:+d} units', 'success')

    except Exception as e:
        logger.error(f"Failed to adjust inventory: {e}")
        db.session.rollback()
        flash('Failed to adjust inventory', 'error')

    return redirect(url_for('administrator.inventory'))


# =============================================================================
# SUBSCRIPTION MANAGEMENT
# =============================================================================

@administrator_bp.route('/subscription')
@handle_database_errors
@log_function_call
def subscription_management():
    """Subscription and plan management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.subscription import Subscription
    from app.models.tenant import Tenant
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        tenant = Tenant.find_by_id(tenant_id)
        subscription = db.session.execute(
            db.select(Subscription).where(Subscription.tenant_id == tenant_id)
        ).scalar_one_or_none()

        return render_template('administrator/subscription.html',
                             tenant=tenant,
                             subscription=subscription)

    except Exception as e:
        logger.error(f"Failed to load subscription info: {e}")
        flash('Failed to load subscription information', 'error')
        return render_template('administrator/subscription.html',
                             tenant=None,
                             subscription=None)

# =============================================================================
# PHASE 2A.2 WORKSHOP OPERATIONS
# =============================================================================

@administrator_bp.route('/jobs')
@validate_pagination
@handle_database_errors
@log_function_call
def jobs(page=1, per_page=25):
    """Workshop job board with workflow filters."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        filter_status = sanitize_input(request.args.get('status', 'all'))
        jobs = _tenant_jobs()

        if filter_status and filter_status != 'all':
            jobs = [job for job in jobs if job.status == filter_status]

        jobs = sorted(jobs, key=lambda job: (job.job_date or date.today(), job.job_id), reverse=True)
        total = len(jobs)
        start = (page - 1) * per_page
        end = start + per_page
        jobs_page = jobs[start:end]
        total_pages = (total + per_page - 1) // per_page

        return render_template(
            'administrator/jobs.html',
            jobs=jobs_page,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            filter_status=filter_status,
            status_summary=_status_summary(jobs),
            current_date=date.today(),
        )
    except Exception as e:
        logger.error(f"Failed to load workshop jobs: {e}")
        flash('Failed to load workshop jobs', 'error')
        return render_template(
            'administrator/jobs.html',
            jobs=[],
            page=1,
            per_page=per_page,
            total=0,
            total_pages=0,
            filter_status='all',
            status_summary={status: 0 for status in Job.VALID_STATUSES},
            current_date=date.today(),
        )


@administrator_bp.route('/bookings', methods=['GET', 'POST'])
@validate_pagination
@handle_database_errors
@log_function_call
def bookings(page=1, per_page=20):
    """Manage workshop bookings and draft work orders."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        if request.method == 'POST':
            customer_id = request.form.get('customer_id', type=int)
            job_date = request.form.get('job_date', type=lambda v: date.fromisoformat(v) if v else None)
            vehicle_id = request.form.get('vehicle_id', type=int) or None
            mileage = request.form.get('mileage', type=int) or None

            if not customer_id or not job_date:
                flash('Customer and job date are required', 'error')
                return redirect(url_for('administrator.bookings'))

            success, errors, job_id = customer_service.schedule_job_for_customer(
                customer_id=customer_id,
                job_date=job_date,
                vehicle_id=vehicle_id,
                mileage=mileage,
                tenant_id=_tenant_id(),
            )
            if success:
                flash(f'Booking created successfully. Job #{job_id}', 'success')
                return redirect(url_for('administrator.job_detail', job_id=job_id))
            for error in errors:
                flash(error, 'error')
            return redirect(url_for('administrator.bookings'))

        customers = _annotate_customers(customer_service.get_all_customers())
        jobs = [job for job in _tenant_jobs() if job.status == Job.STATUS_DRAFT or (job.job_date >= date.today() and not job.completed)]
        jobs = sorted(jobs, key=lambda job: (job.job_date or date.today(), job.job_id))

        start = (page - 1) * per_page
        end = start + per_page
        jobs_page = jobs[start:end]
        total_pages = (len(jobs) + per_page - 1) // per_page

        return render_template(
            'administrator/bookings.html',
            customers=customers,
            bookings=jobs_page,
            page=page,
            per_page=per_page,
            total=len(jobs),
            total_pages=total_pages,
            current_date=date.today(),
        )
    except Exception as e:
        logger.error(f"Failed to load bookings: {e}")
        flash('Failed to load bookings', 'error')
        return render_template(
            'administrator/bookings.html',
            customers=[],
            bookings=[],
            page=1,
            per_page=per_page,
            total=0,
            total_pages=0,
            current_date=date.today(),
        )


@administrator_bp.route('/vehicles')
@validate_pagination
@handle_database_errors
@log_function_call
def vehicles(page=1, per_page=25):
    """Vehicle management view with service history visibility."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        customers = customer_service.get_all_customers()
        rows = _tenant_vehicle_rows(customers)
        total = len(rows)
        start = (page - 1) * per_page
        end = start + per_page
        rows_page = rows[start:end]
        total_pages = (total + per_page - 1) // per_page

        return render_template(
            'administrator/vehicles.html',
            vehicle_rows=rows_page,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            current_date=date.today(),
        )
    except Exception as e:
        logger.error(f"Failed to load vehicles: {e}")
        flash('Failed to load vehicles', 'error')
        return render_template(
            'administrator/vehicles.html',
            vehicle_rows=[],
            page=1,
            per_page=per_page,
            total=0,
            total_pages=0,
            current_date=date.today(),
        )


@administrator_bp.route('/jobs/<int:job_id>')
@handle_database_errors
@log_function_call
def job_detail(job_id):
    """Workshop job detail with workflow actions."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        job_details = job_service.get_job_details(job_id)
        if not job_details:
            flash('Job does not exist', 'error')
            return redirect(url_for('administrator.jobs'))

        return render_template(
            'administrator/job_detail.html',
            data=job_details.get('job_info', {}),
            services=job_details.get('services', []),
            parts=job_details.get('parts', []),
            attachments=job_details.get('attachments', []),
            status_history=job_details.get('status_history', []),
            available_technicians=job_details.get('available_technicians', []),
            all_services=job_details.get('all_services', []),
            all_parts=job_details.get('all_parts', []),
            services_total=job_details.get('services_total', 0),
            parts_total=job_details.get('parts_total', 0),
            estimated_total=job_details.get('estimated_total', 0),
            job_completed=job_details.get('job_completed', False),
            job_details=job_details,
            status_options=Job.STATUS_LABELS,
        )
    except Exception as e:
        logger.error(f"Failed to load workshop job details (ID: {job_id}): {e}")
        flash('Failed to load job details', 'error')
        return redirect(url_for('administrator.jobs'))


@administrator_bp.route('/jobs/<int:job_id>/status', methods=['POST'])
@handle_database_errors
def update_job_status(job_id):
    """Update a workshop job workflow state."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        new_status = sanitize_input(request.form.get('status', '')).strip()
        note = sanitize_input(request.form.get('note', '')).strip() or None
        success, errors, _ = job_service.update_job_status(
            job_id,
            new_status,
            changed_by_user_id=session.get('user_id'),
            note=note,
        )
        if success:
            flash('Job status updated.', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to update workshop job status: {e}")
        flash('Failed to update job status', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/assign-technician', methods=['POST'])
@handle_database_errors
def assign_job_technician(job_id):
    """Assign a technician to a workshop job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        technician_id = request.form.get('technician_id', type=int)
        success, errors, _ = job_service.assign_technician_to_job(
            job_id,
            technician_id,
            changed_by_user_id=session.get('user_id'),
        )
        if success:
            flash('Technician assignment updated.', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to assign technician: {e}")
        flash('Failed to assign technician', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/notes', methods=['POST'])
@handle_database_errors
def add_job_note(job_id):
    """Append an internal note to a workshop job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        note = sanitize_input(request.form.get('note', '')).strip()
        if not note:
            flash('Please enter an internal note', 'warning')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors, _ = job_service.add_internal_note_to_job(
            job_id,
            note,
            changed_by_user_id=session.get('user_id'),
        )
        if success:
            flash('Internal note added.', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to add job note: {e}")
        flash('Failed to add note', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/add-service', methods=['POST'])
@handle_database_errors
def add_job_service(job_id):
    """Add a service to a workshop job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        service_id = request.form.get('service_id', type=int)
        quantity = request.form.get('quantity', type=int)
        if not service_id or not validate_positive_integer(service_id):
            flash('Please select a valid service', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))
        if not quantity or not validate_positive_integer(quantity):
            flash('Please enter a valid quantity', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.add_service_to_job(job_id, service_id, quantity)
        if success:
            flash('Service added successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to add workshop service: {e}")
        flash('Failed to add service', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/add-part', methods=['POST'])
@handle_database_errors
def add_job_part(job_id):
    """Add a part to a workshop job."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        part_id = request.form.get('part_id', type=int)
        quantity = request.form.get('quantity', type=int)
        if not part_id or not validate_positive_integer(part_id):
            flash('Please select a valid part', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))
        if not quantity or not validate_positive_integer(quantity):
            flash('Please enter a valid quantity', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.add_part_to_job(job_id, part_id, quantity)
        if success:
            flash('Part added successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to add workshop part: {e}")
        flash('Failed to add part', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/update-service', methods=['POST'])
@handle_database_errors
def update_job_service(job_id):
    """Update a workshop job service quantity."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        service_id = request.form.get('service_id', type=int)
        quantity = request.form.get('quantity', type=int)
        if not service_id or not validate_positive_integer(service_id):
            flash('Please select a valid service', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))
        if quantity is None or quantity < 0:
            flash('Please enter a valid quantity', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.update_service_quantity(job_id, service_id, quantity)
        if success:
            flash('Service updated successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to update workshop service quantity: {e}")
        flash('Failed to update service', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/remove-service', methods=['POST'])
@handle_database_errors
def remove_job_service(job_id):
    """Remove a workshop job service."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        service_id = request.form.get('service_id', type=int)
        if not service_id or not validate_positive_integer(service_id):
            flash('Please select a valid service', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.remove_service_from_job(job_id, service_id)
        if success:
            flash('Service removed successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to remove workshop service: {e}")
        flash('Failed to remove service', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/update-part', methods=['POST'])
@handle_database_errors
def update_job_part(job_id):
    """Update a workshop job part quantity."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        part_id = request.form.get('part_id', type=int)
        quantity = request.form.get('quantity', type=int)
        if not part_id or not validate_positive_integer(part_id):
            flash('Please select a valid part', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))
        if quantity is None or quantity < 0:
            flash('Please enter a valid quantity', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.update_part_quantity(job_id, part_id, quantity)
        if success:
            flash('Part updated successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to update workshop part quantity: {e}")
        flash('Failed to update part', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


@administrator_bp.route('/jobs/<int:job_id>/remove-part', methods=['POST'])
@handle_database_errors
def remove_job_part(job_id):
    """Remove a workshop job part."""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        part_id = request.form.get('part_id', type=int)
        if not part_id or not validate_positive_integer(part_id):
            flash('Please select a valid part', 'error')
            return redirect(url_for('administrator.job_detail', job_id=job_id))

        success, errors = job_service.remove_part_from_job(job_id, part_id)
        if success:
            flash('Part removed successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))
    except Exception as e:
        logger.error(f"Failed to remove workshop part: {e}")
        flash('Failed to remove part', 'error')
        return redirect(url_for('administrator.job_detail', job_id=job_id))


