"""
Main Routes Blueprint
Contains home page, login, public functionality routes
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, g
from datetime import date
import logging
from app.services.customer_service import CustomerService
from app.services.job_service import JobService
from app.services.billing_service import BillingService
from app.models.user import User
from app.utils.decorators import handle_database_errors, log_function_call
from app.utils.roles import current_role_name, is_superadmin_session, is_platform_admin_session, get_role_dashboard, resolve_effective_role, set_session_role, TENANT_ADMIN_ROLE, CUSTOMER_ROLE, SUPERADMIN_ROLE
from app.utils.validators import validate_customer_data, sanitize_input
from app.utils.security import require_auth, InputSanitizer, SQLInjectionProtection
from app.utils.decorators import login_required, tenant_required
from app.utils.error_handler import ValidationError, BusinessLogicError

# Create blueprint
main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

# Initialize services
customer_service = CustomerService()
job_service = JobService()
billing_service = BillingService()


def _get_current_authenticated_user():
    """Return the authenticated user from the current request/session if available."""
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


def _resolve_portal_role(default: str | None = None) -> str | None:
    """Resolve and persist the most accurate portal role for the current session."""
    user = _get_current_authenticated_user()
    resolved = resolve_effective_role(
        user=user,
        tenant_id=session.get('current_tenant_id'),
        default=default or current_role_name(),
    )
    if resolved:
        set_session_role(resolved)
    return resolved


@main_bp.route('/')
@handle_database_errors
@log_function_call
def index():
    """Home page - Display system overview and quick statistics"""
    try:
        user_type = _resolve_portal_role(default='customer') or current_role_name('customer')

        if session.get('logged_in'):
            if user_type == SUPERADMIN_ROLE or is_superadmin_session():
                return redirect(url_for('platform.home'))
            if user_type == CUSTOMER_ROLE:
                return redirect(url_for('customer.dashboard'))
            return redirect(url_for('main.dashboard'))

        # Get system statistics
        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()
        
        # Get recent work orders
        recent_jobs, _, _ = job_service.get_current_jobs(page=1, per_page=5)
        
        # Get overdue bills
        overdue_bills = billing_service.get_overdue_bills()[:5]
        
        return render_template('index.html',
                             job_stats=job_stats,
                             billing_stats=billing_stats,
                             recent_jobs=recent_jobs,
                             overdue_bills=overdue_bills,
                             current_date=date.today())
        
    except Exception as e:
        logger.error(f"Home page loading failed: {e}")
        flash('System temporarily unavailable, please try again later', 'error')
        return render_template('index.html',
                             job_stats={},
                             billing_stats={},
                             recent_jobs=[],
                             overdue_bills=[],
                             current_date=date.today())


@main_bp.route('/login')
def login():
    """Redirect to auth login page"""
    return redirect(url_for('auth.login'))


@main_bp.route('/logout')
def logout():
    """Logout - clear session and redirect"""
    # Clear the Flask server-side session.
    # The Supabase client-side session is cleared by supabase-auth.js
    # calling supabase.auth.signOut() after this redirect completes.
    session.clear()
    flash('You have successfully logged out', 'info')
    return redirect(url_for('main.index'))


@main_bp.route('/dashboard')
@require_auth()
@handle_database_errors
@log_function_call
def dashboard():
    """Dashboard dispatcher - route users to the correct portal dashboard."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('auth.login'))

    try:
        user = _get_current_authenticated_user()
        user_type = _resolve_portal_role(default=current_role_name('technician') or 'technician') or current_role_name('technician') or 'technician'

        if user_type == CUSTOMER_ROLE:
            return redirect(url_for('customer.dashboard'))
        if user_type == SUPERADMIN_ROLE:
            return redirect(url_for('platform.home'))
        if user_type == TENANT_ADMIN_ROLE or is_platform_admin_session() or user_type in ('manager', 'owner', 'admin'):
            return redirect(url_for('administrator.dashboard'))
        if user_type == 'technician':
            return redirect(url_for('technician.dashboard'))

        return redirect(url_for('main.index'))

    except Exception as e:
        logger.error(f"Dashboard loading failed: {e}")
        flash('Failed to load dashboard', 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/api/search/customers')
@require_auth()
@handle_database_errors
def api_search_customers():
    """API: Search customers"""
    query = InputSanitizer.sanitize_string(request.args.get('q', ''))
    search_type = InputSanitizer.sanitize_string(request.args.get('type', 'both'))
    
    # Check for SQL injection
    if SQLInjectionProtection.scan_sql_injection(query):
        raise ValidationError("Search criteria contains illegal characters")
    
    if not query:
        return jsonify([])
    
    try:
        customers = customer_service.search_customers(query, search_type)
        return jsonify([{
            'customer_id': c.customer_id,
            'full_name': c.full_name,
            'email': c.email,
            'phone': c.phone
        } for c in customers])
        
    except Exception as e:
        logger.error(f"Customer search failed: {e}")
        return jsonify({'error': 'Search failed'}), 500


@main_bp.route('/api/customers/<int:customer_id>')
@login_required
@handle_database_errors
def api_get_customer(customer_id):
    """API: Get customer details"""
    try:
        customer = customer_service.get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        stats = customer_service.get_customer_statistics(customer_id)
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Get customer details failed: {e}")
        return jsonify({'error': 'Failed to get customer information'}), 500


@main_bp.route('/api/customers/<int:customer_id>/vehicles')
@login_required
@handle_database_errors
def api_get_customer_vehicles(customer_id):
    """API: Get all vehicles for a customer."""
    try:
        customer = customer_service.get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        vehicles = customer_service.get_customer_vehicles(customer_id)
        return jsonify([
            {
                'vehicle_id': vehicle.vehicle_id,
                'display_name': vehicle.display_name,
                'registration_number': vehicle.registration_number,
                'make': vehicle.make,
                'model': vehicle.model,
                'year': vehicle.year,
                'mileage': vehicle.mileage,
                'is_primary': bool(vehicle.is_primary),
            }
            for vehicle in vehicles
        ])

    except Exception as e:
        logger.error(f"Get customer vehicles failed: {e}")
        return jsonify({'error': 'Failed to get vehicles'}), 500


@main_bp.route('/customers')
@login_required
@handle_database_errors
@log_function_call
def customers():
    """Customer list page"""
    try:
        # Get search parameters
        search_query = sanitize_input(request.args.get('search', ''))
        search_type = sanitize_input(request.args.get('search_type', 'both'))
        
        # Search or get all customers
        if search_query:
            customers = customer_service.search_customers(search_query, search_type)
        else:
            customers = customer_service.get_all_customers()
        
        return render_template('customers/list.html',
                             customers=customers,
                             search_query=search_query,
                             search_type=search_type)
        
    except Exception as e:
        logger.error(f"Customer list loading failed: {e}")
        flash('Failed to load customer list', 'error')
        return render_template('customers/list.html',
                             customers=[],
                             search_query='',
                             search_type='both')


@main_bp.route('/customers/new')
@login_required
def new_customer():
    """New customer page"""
    return render_template('customers/form.html',
                         customer=None,
                         action='create')


@main_bp.route('/customers', methods=['POST'])
@login_required
@handle_database_errors
def create_customer():
    """Create new customer"""
    # Get form data
    customer_data = {
        'first_name': sanitize_input(request.form.get('first_name', '')),
        'family_name': sanitize_input(request.form.get('family_name', '')),
        'email': sanitize_input(request.form.get('email', '')),
        'phone': sanitize_input(request.form.get('phone', ''))
    }
    
    try:
        # Validate data
        validation_result = validate_customer_data(customer_data)
        if not validation_result.is_valid:
            for error in validation_result.get_errors():
                flash(error, 'error')
            return render_template('customers/form.html',
                                 customer=customer_data,
                                 action='create')
        
        # Create customer
        success, errors, customer = customer_service.create_customer(customer_data)
        
        if success:
            flash(f'Customer {customer.full_name} created successfully!', 'success')
            return redirect(url_for('main.customers'))
        else:
            for error in errors:
                flash(error, 'error')
            return render_template('customers/form.html',
                                 customer=customer_data,
                                 action='create')
            
    except Exception as e:
        logger.error(f"Failed to create customer: {e}")
        flash('Failed to create customer, please try again later', 'error')
        return render_template('customers/form.html',
                             customer=customer_data,
                             action='create')


def _load_customer_for_scope(customer_id: int):
    """Load a customer and enforce tenant scope for non-superadmin sessions."""
    customer = customer_service.get_customer_by_id(customer_id)
    if not customer:
        flash('Customer not found', 'error')
        return None, redirect(url_for('main.customers'))

    if is_superadmin_session():
        return customer, None

    tenant_id = getattr(g, 'current_tenant_id', None) or session.get('current_tenant_id')
    if not tenant_id:
        flash('Please select an organization first', 'warning')
        return None, redirect(url_for('auth.select_tenant'))

    if getattr(customer, 'tenant_id', None) != tenant_id:
        flash('Customer not found', 'error')
        return None, redirect(url_for('main.customers'))

    return customer, None


@main_bp.route('/customers/<int:customer_id>')
@login_required
@handle_database_errors
@log_function_call
def customer_detail(customer_id):
    """Customer detail page"""
    try:
        customer, redirect_response = _load_customer_for_scope(customer_id)
        if redirect_response:
            return redirect_response

        # Get customer statistics
        stats = customer_service.get_customer_statistics(customer_id)
        vehicles = customer_service.get_customer_vehicles(customer_id)
        vehicle_histories = {
            vehicle.vehicle_id: customer_service.get_vehicle_history(vehicle.vehicle_id)
            for vehicle in vehicles
        }
        portal_status = customer_service.get_portal_access_status(customer_id)

        return render_template('customers/detail.html',
                             customer=customer,
                             stats=stats,
                             vehicles=vehicles,
                             job_history=customer.get_jobs(),
                             vehicle_histories=vehicle_histories,
                             portal_status=portal_status)

    except Exception as e:
        logger.error(f"Customer detail loading failed: {e}")
        flash('Failed to load customer details', 'error')
        return redirect(url_for('main.customers'))


@main_bp.route('/customers/<int:customer_id>/portal-access', methods=['POST'])
@login_required
@handle_database_errors
def enable_customer_portal_access(customer_id):
    """Enable portal access for a customer."""
    if not is_platform_admin_session():
        flash('Administrator privileges required', 'error')
        return redirect(url_for('main.customer_detail', customer_id=customer_id))

    try:
        customer, redirect_response = _load_customer_for_scope(customer_id)
        if redirect_response:
            return redirect_response

        redirect_to = url_for('auth.reset_password', _external=True)
        # Supabase rejects non-HTTPS redirect URLs. url_for may produce http://
        # on Render behind a proxy — force https:// to ensure the link works.
        if redirect_to.startswith('http://'):
            redirect_to = 'https://' + redirect_to[7:]
        success, errors, user, details = customer_service.enable_portal_access(
            customer_id=customer_id,
            redirect_to=redirect_to,
        )

        provisioning_status = details.get('provisioning_status')
        if success:
            if provisioning_status == 'ready':
                if details.get('supabase_recover_ok'):
                    flash(f"Portal access enabled for {customer.full_name}. A password setup email was sent to {customer.email}.", 'success')
                elif details.get('supabase_sign_up_ok'):
                    flash(f"Portal access enabled for {customer.full_name}. A portal account was created for {customer.email}.", 'success')
                else:
                    flash(f"Portal access enabled for {customer.full_name}.", 'success')
            elif provisioning_status == 'partial':
                warnings = '; '.join(details.get('remote_errors') or details.get('provisioning_notes') or [])
                temp_password = details.get('temp_password')
                extra = f" Temporary password: {temp_password}" if temp_password else ''
                flash(
                    f"Portal access was linked for {customer.full_name}, but provisioning completed with warnings. {warnings}{extra}".strip(),
                    'warning'
                )
            else:
                warnings = '; '.join(details.get('remote_errors') or details.get('provisioning_notes') or [])
                temp_password = details.get('temp_password')
                extra = f" Temporary password: {temp_password}" if temp_password else ''
                flash(
                    f"Portal access was linked for {customer.full_name}, but provisioning is not complete. {warnings}{extra}".strip(),
                    'warning'
                )
        else:
            if details.get('local_account_linked'):
                warnings = '; '.join(details.get('remote_errors') or details.get('provisioning_notes') or [])
                temp_password = details.get('temp_password')
                extra = f" Temporary password: {temp_password}" if temp_password else ''
                flash(
                    f"Portal access was linked for {customer.full_name}, but external provisioning failed. {warnings}{extra}".strip(),
                    'warning'
                )
            else:
                for error in errors:
                    flash(error, 'error')

        return redirect(url_for('main.customer_detail', customer_id=customer_id))

    except Exception as e:
        logger.error(f"Failed to enable portal access for customer {customer_id}: {e}")
        flash('Failed to enable portal access, please try again later', 'error')
        return redirect(url_for('main.customer_detail', customer_id=customer_id))


@main_bp.route('/customers/<int:customer_id>/vehicles', methods=['POST'])
@login_required
@handle_database_errors
def create_vehicle(customer_id):
    """Create a new vehicle for a customer."""
    try:
        customer, redirect_response = _load_customer_for_scope(customer_id)
        if redirect_response:
            return redirect_response

        def _opt(val: str):
            """Return None for blank optional strings."""
            return val.strip() or None

        vehicle_data = {
            'customer_id': customer_id,
            'tenant_id': getattr(customer, 'preferred_tenant_id', None) or getattr(customer, 'tenant_id', None),
            'make': sanitize_input(request.form.get('make', '')),
            'model': sanitize_input(request.form.get('model', '')),
            'year': request.form.get('year', type=int) or None,
            'registration_number': _opt(sanitize_input(request.form.get('registration_number', ''))),
            'vin': _opt(sanitize_input(request.form.get('vin', ''))),
            'color': _opt(sanitize_input(request.form.get('color', ''))),
            'mileage': request.form.get('mileage', type=int) or None,
            'notes': _opt(sanitize_input(request.form.get('notes', ''))),
            'is_primary': bool(request.form.get('is_primary'))
        }

        success, errors, vehicle = customer_service.create_vehicle(vehicle_data)
        if success:
            flash(f'Vehicle {vehicle.display_name} added successfully!', 'success')
        else:
            for error in errors:
                flash(error, 'error')

        return redirect(url_for('main.customer_detail', customer_id=customer_id))

    except Exception as e:
        logger.error(f"Failed to create vehicle: {e}")
        flash('Failed to add vehicle, please try again later', 'error')
        return redirect(url_for('main.customer_detail', customer_id=customer_id))


@main_bp.route('/customers/<int:customer_id>/edit')
@login_required
@handle_database_errors
def edit_customer(customer_id):
    """Edit customer page"""
    try:
        customer, redirect_response = _load_customer_for_scope(customer_id)
        if redirect_response:
            return redirect_response
        
        return render_template('customers/form.html',
                             customer=customer,
                             action='edit')
        
    except Exception as e:
        logger.error(f"Failed to load customer edit page: {e}")
        flash('Failed to load edit page', 'error')
        return redirect(url_for('main.customers'))


@main_bp.route('/customers/<int:customer_id>', methods=['POST'])
@login_required
@handle_database_errors
def update_customer(customer_id):
    """Update customer information"""
    # Get form data
    customer_data = {
        'first_name': sanitize_input(request.form.get('first_name', '')),
        'family_name': sanitize_input(request.form.get('family_name', '')),
        'email': sanitize_input(request.form.get('email', '')),
        'phone': sanitize_input(request.form.get('phone', ''))
    }
    
    try:
        customer, redirect_response = _load_customer_for_scope(customer_id)
        if redirect_response:
            return redirect_response

        # Validate data
        validation_result = validate_customer_data(customer_data)
        if not validation_result.is_valid:
            for error in validation_result.get_errors():
                flash(error, 'error')
            return render_template('customers/form.html',
                                 customer=customer,
                                 action='edit')
        
        # Update customer
        success, errors, customer = customer_service.update_customer(customer_id, customer_data)
        
        if success:
            flash(f'Customer {customer.full_name} updated successfully!', 'success')
            return redirect(url_for('main.customer_detail', customer_id=customer_id))
        else:
            for error in errors:
                flash(error, 'error')
            customer = customer_service.get_customer_by_id(customer_id)
            return render_template('customers/form.html',
                                 customer=customer,
                                 action='edit')
            
    except Exception as e:
        logger.error(f"Failed to update customer: {e}")
        flash('Failed to update customer, please try again later', 'error')
        customer = customer_service.get_customer_by_id(customer_id)
        return render_template('customers/form.html',
                             customer=customer,
                             action='edit')


@main_bp.route('/about')
def about():
    """About page"""
    return render_template('about.html')


@main_bp.route('/help')
def help_page():
    """Help page"""
    return render_template('help.html')


# Error handling
@main_bp.errorhandler(404)
def not_found_error(error):
    """404 error handler"""
    return render_template('errors/404.html'), 404


@main_bp.errorhandler(500)
def internal_error(error):
    """500 error handler"""
    return render_template('errors/500.html'), 500 