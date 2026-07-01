"""
Decorator utility module
Contains decorators for authentication, authorization, logging, and error handling
"""
import functools
import logging
from typing import Callable, Any, List, Union
import time
from flask import jsonify, flash, request, session, redirect, url_for, abort, g, render_template
from app.utils.database import DatabaseError, ValidationError as DatabaseValidationError
from app.utils.error_handler import ValidationError as AppValidationError
from app.services.authorization_service import authorization_service
from app.services.tenant_context_service import _tenant_context_service
from app.utils.roles import normalize_role, can_access_admin_portal, can_access_staff_portal, can_access_superadmin_portal, current_role_name


def handle_database_errors(func: Callable) -> Callable:
    """Database error handling decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DatabaseError as e:
            logging.error(f"Database error in {func.__name__}: {e}")
            flash(f"Database operation failed: {e}", 'error')
            return None
        except (DatabaseValidationError, AppValidationError) as e:
            logging.warning(f"Validation error in {func.__name__}: {e}")
            flash(f"Data validation failed: {e}", 'warning')
            return None
        except Exception as e:
            logging.exception(f"Unknown error in {func.__name__}: {e}")
            flash("System error, please try again later", 'error')
            try:
                return render_template('errors/500.html'), 500
            except Exception:
                return 'Internal Server Error', 500

    return wrapper


def log_function_call(func: Callable) -> Callable:
    """Function call logging decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(func.__module__)
        start_time = time.time()

        logger.debug(f"Calling {func.__name__}")

        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            logger.debug(f"{func.__name__} completed in {execution_time:.3f}s")
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"{func.__name__} failed in {execution_time:.3f}s: {e}")
            raise

    return wrapper


def require_json(func: Callable) -> Callable:
    """Require JSON request decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON'}), 400
        return func(*args, **kwargs)

    return wrapper


def validate_form_data(validation_func: Callable) -> Callable:
    """Form data validation decorator"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            form_data = request.form.to_dict()
            validation_result = validation_func(form_data)
            if not validation_result.is_valid:
                for error in validation_result.get_errors():
                    flash(error, 'error')
                return None
            return func(*args, **kwargs)
        return wrapper
    return decorator


def cache_result(timeout: int = 300) -> Callable:
    """Simple in-memory cache decorator"""
    def decorator(func: Callable) -> Callable:
        cache = {}

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            current_time = time.time()

            if cache_key in cache:
                cached_time, cached_result = cache[cache_key]
                if current_time - cached_time < timeout:
                    return cached_result
                else:
                    del cache[cache_key]

            result = func(*args, **kwargs)
            cache[cache_key] = (current_time, result)
            return result

        return wrapper
    return decorator


def retry_on_failure(max_retries: int = 3, delay: float = 1.0) -> Callable:
    """Retry on database failure decorator"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except DatabaseError as e:
                    last_exception = e
                    if attempt < max_retries:
                        logging.warning(f"{func.__name__} attempt {attempt + 1} failed: {e}")
                        time.sleep(delay)
                    else:
                        logging.error(f"{func.__name__} failed after {max_retries} retries")
                        raise last_exception
                except Exception as e:
                    raise e

            raise last_exception

        return wrapper
    return decorator


def measure_performance(func: Callable) -> Callable:
    """Performance measurement decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = get_memory_usage()

        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.time()
            end_memory = get_memory_usage()

            execution_time = end_time - start_time
            memory_diff = end_memory - start_memory

            logger = logging.getLogger(func.__module__)
            logger.info(f"Performance - {func.__name__}: "
                       f"time={execution_time:.3f}s, "
                       f"memory={memory_diff:.2f}MB")

    return wrapper


def get_memory_usage() -> float:
    """Get current memory usage in MB"""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def validate_pagination(func: Callable) -> Callable:
    """Pagination parameter validation decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)

        if page < 1:
            page = 1
        if per_page < 1 or per_page > 100:
            per_page = 10

        kwargs['page'] = page
        kwargs['per_page'] = per_page

        return func(*args, **kwargs)

    return wrapper


def login_required(func: Callable) -> Callable:
    """Login required decorator - ensures user is authenticated"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please login to access this page', 'warning')
            return redirect(url_for('auth.login'))
        return func(*args, **kwargs)

    return wrapper


def tenant_required(func: Callable) -> Callable:
    """
    Tenant context required decorator.
    Ensures g.current_tenant_id is set before proceeding.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please login to access this page', 'warning')
            return redirect(url_for('auth.login'))

        tenant_id = getattr(g, 'current_tenant_id', None) or session.get('current_tenant_id')
        if tenant_id:
            if getattr(g, 'current_tenant', None) is None:
                _tenant_context_service.set_active_tenant(int(tenant_id))
        else:
            flash('Please select an organization first', 'warning')
            return redirect(url_for('auth.select_tenant'))

        return func(*args, **kwargs)

    return wrapper


def permission_required(permission: str) -> Callable:
    """
    Permission-based access control decorator.
    Checks that the current user has the specified permission in the current tenant.

    Usage:
        @permission_required('manage_jobs')
        def create_job():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.is_json:
                    return jsonify({'error': 'Authentication required'}), 401
                flash('Please login to access this page', 'warning')
                return redirect(url_for('auth.login'))

            ctx = _tenant_context_service.get_context()
            if getattr(ctx, 'tenant_id', None) is None and permission not in {'reports.view', 'audit.view'}:
                if request.is_json:
                    return jsonify({'error': 'No organization selected'}), 403
                flash('Please select an organization first', 'warning')
                return redirect(url_for('auth.select_tenant'))

            decision = authorization_service.authorize(
                permission,
                user=getattr(ctx, 'user', None),
                tenant_id=getattr(ctx, 'tenant_id', None),
                membership=getattr(ctx, 'membership', None),
            )
            if not decision.allowed:
                logging.warning(
                    f"Permission denied: user {session.get('user_id')} role '{decision.role}' "
                    f"lacks '{permission}' in tenant {decision.tenant_id}: {decision.reason}"
                )
                if request.is_json:
                    return jsonify({'error': 'Insufficient permissions'}), 403
                flash('You do not have permission to access this page', 'error')
                abort(403)

            return func(*args, **kwargs)

        return wrapper
    return decorator


def role_required(*roles: str) -> Callable:
    """Role-based access control decorator (legacy support)"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                flash('Please login to access this page', 'warning')
                return redirect(url_for('auth.login'))

            user_role = current_role_name()
            if normalize_role(user_role) not in {normalize_role(r) for r in roles}:
                logging.warning(
                    f"Access denied: user role '{user_role}' not in {roles} "
                    f"for {func.__name__}"
                )
                flash('You do not have permission to access this page', 'error')
                abort(403)

            return func(*args, **kwargs)

        return wrapper
    return decorator


def superadmin_required(func: Callable) -> Callable:
    """Platform superadmin permission decorator."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please login to access this page', 'warning')
            return redirect(url_for('auth.login'))

        if not can_access_superadmin_portal():
            logging.warning(
                f"Superadmin access denied for {func.__name__}"
            )
            flash('Platform Owner permission required', 'error')
            abort(403)

        return func(*args, **kwargs)

    return wrapper


def admin_required(func: Callable) -> Callable:
    """Administrator permission decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please login to access this page', 'warning')
            return redirect(url_for('auth.login'))

        if not can_access_admin_portal():
            user_role = current_role_name()
            logging.warning(
                f"Admin access denied: user role '{user_role}' for {func.__name__}"
            )
            flash('Administrator permission required', 'error')
            abort(403)

        return func(*args, **kwargs)

    return wrapper


def technician_required(func: Callable) -> Callable:
    """Technician or higher permission decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please login to access this page', 'warning')
            return redirect(url_for('auth.login'))

        if not can_access_staff_portal():
            user_role = current_role_name()
            logging.warning(
                f"Technician access denied: user role '{user_role}' for {func.__name__}"
            )
            flash('Technician permission required', 'error')
            abort(403)

        return func(*args, **kwargs)

    return wrapper
