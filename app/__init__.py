"""
Automotive Repair Management System
Flask Application Factory and Initialization
"""
from flask import Flask
from html import unescape as html_unescape
from sqlalchemy.pool import NullPool
from werkzeug.middleware.proxy_fix import ProxyFix
from config.base import get_config
from app.extensions import db
from app.utils.error_handler import ErrorHandler, LoggerConfig
from app.utils.security import SecurityConfig, CSRFProtection
from app.utils.roles import current_role_name, is_superadmin_session, is_platform_admin_session, is_staff_session
from app.domain import CANONICAL_DOMAINS, PORTALS, PORTAL_MODULES, PERMISSION_CATALOG, USER_TYPES, ACCESS_LEVELS
import os


def create_app(config_name=None):
    """Application Factory Function"""
    app = Flask(__name__)

    # Trust reverse proxy headers (Heroku/Cloudflare)
    # This ensures url_for(_external=True) generates correct https:// URLs
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Load configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    config = get_config(config_name)
    app.config.from_object(config)

    # Validate configuration
    if hasattr(config, 'validate_config'):
        config.validate_config()

    # Ensure secret key is set
    if not app.config.get('SECRET_KEY'):
        if config_name == 'production':
            raise ValueError("SECRET_KEY must be set in production environment")
        app.config['SECRET_KEY'] = getattr(config, 'SECRET_KEY', None)
        if not app.config['SECRET_KEY']:
            raise ValueError("SECRET_KEY is required")

    # Configure SQLAlchemy database URI
    _configure_database(app, config)

    # Initialize extensions
    init_extensions(app)

    # Register blueprints
    register_blueprints(app)

    # Register error handlers
    register_error_handlers(app)

    # Register security middleware
    register_security_middleware(app)

    # Register tenant middleware
    from app.middleware.tenant import init_tenant_middleware
    init_tenant_middleware(app)

    app.logger.info("Application initialization complete")

    return app


def _configure_database(app, config):
    """Configure SQLAlchemy database URI"""
    database_url = os.environ.get('DATABASE_URL')

    if database_url:
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    elif app.config.get('SQLALCHEMY_DATABASE_URI'):
        # Config class already set a URI (e.g. TestingConfig uses sqlite)
        pass
    else:
        db_user = config.DB_USER
        db_password = config.DB_PASSWORD
        db_host = config.DB_HOST
        db_port = config.DB_PORT
        db_name = config.DB_NAME
        app.config['SQLALCHEMY_DATABASE_URI'] = (
            f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        )

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ECHO'] = app.config.get('DEBUG', False)

    sslmode = getattr(config, 'DB_SSLMODE', 'require')
    if sslmode and sslmode != 'disable':
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'connect_args': {'sslmode': sslmode},
            'poolclass': NullPool,
        }


def init_extensions(app):
    """Initialize Flask extensions"""
    LoggerConfig.setup_logging(app)

    db.init_app(app)

    with app.app_context():
        from app.models import (
            Customer, Job, JobService, JobPart, Service, Part, User, AuditLog,
            Tenant, Vehicle, TenantMembership, Inventory, InventoryTransaction, Subscription
        )

        if os.environ.get('FLASK_ENV', 'development') != 'production':
            db.create_all()

    # Initialize platform bootstrap helpers (idempotent).
    try:
        from app.services.bootstrap_service import bootstrap_platform_superadmin
        bootstrap_platform_superadmin(app)
    except Exception as exc:
        app.logger.warning(f"SuperAdmin bootstrap skipped: {exc}")

    # Initialize Supabase Auth service
    from app.services.auth_service import supabase_auth
    supabase_auth.init_app(app)

    ErrorHandler(app)


def register_blueprints(app):
    """Register blueprints"""
    from app.views.main import main_bp
    from app.views.superadmin import superadmin_bp
    from app.views.technician import technician_bp
    from app.views.administrator import administrator_bp
    from app.views.customer import customer_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(superadmin_bp, url_prefix='/admin')
    app.register_blueprint(technician_bp, url_prefix='/technician')
    app.register_blueprint(administrator_bp, url_prefix='/administrator')
    app.register_blueprint(customer_bp, url_prefix='/customer')

    # Also register tenant-scoped versions
    # url_value_preprocessor consumes 'tenant_slug' so views don't need to accept it
    app.register_blueprint(technician_bp, url_prefix='/org/<tenant_slug>/technician',
                          name='tenant_technician')
    app.register_blueprint(administrator_bp, url_prefix='/org/<tenant_slug>/administrator',
                          name='tenant_administrator')
    app.register_blueprint(customer_bp, url_prefix='/org/<tenant_slug>/customer',
                          name='tenant_customer')

    @app.url_value_preprocessor
    def pop_tenant_slug(endpoint, values):
        """Remove tenant_slug from URL values so views don't need to accept it.
        The tenant middleware already resolves the tenant from the URL path."""
        if values:
            values.pop('tenant_slug', None)

    # Register auth blueprint
    try:
        from app.views.auth import auth_bp
        app.register_blueprint(auth_bp, url_prefix='/auth')
    except ImportError:
        pass

    # Register billing blueprint
    try:
        from app.views.billing import billing_bp
        app.register_blueprint(billing_bp, url_prefix='/billing')
        app.register_blueprint(billing_bp, url_prefix='/org/<tenant_slug>/billing',
                              name='tenant_billing')
    except ImportError:
        pass

    # Register onboarding blueprint
    try:
        from app.views.onboarding import onboarding_bp
        app.register_blueprint(onboarding_bp, url_prefix='/onboarding')
    except ImportError:
        pass

    # Register platform portal alias (foundation shell for the new architecture)
    try:
        from app.views.platform import platform_bp
        app.register_blueprint(platform_bp, url_prefix='/platform')
    except ImportError:
        pass


def register_error_handlers(app):
    """Register error handlers"""
    pass


def register_security_middleware(app):
    """Register security middleware"""

    @app.after_request
    def apply_security_headers(response):
        return SecurityConfig.apply_security_headers(response)

    @app.context_processor
    def inject_csrf_token():
        return {'csrf_token': CSRFProtection.generate_token()}

    @app.context_processor
    def inject_role_flags():
        """Expose normalized role helpers to Jinja templates."""
        return {
            'current_role_name': current_role_name(),
            'is_superadmin_user': is_superadmin_session(),
            'is_platform_admin': is_platform_admin_session(),
            'is_staff_user': is_staff_session(),
        }

    @app.context_processor
    def inject_notifications():
        """Inject notification counts for the navbar"""
        from flask import session
        notification_data = {
            'notification_count': 0,
            'overdue_bills_count': 0,
            'unpaid_bills_count': 0,
        }
        if not session.get('logged_in'):
            return notification_data
        try:
            from app.services.billing_service import BillingService
            billing_service = BillingService()
            overdue = billing_service.get_overdue_bills()
            unpaid = billing_service.get_unpaid_bills()
            overdue_count = len(overdue) if overdue else 0
            unpaid_count = len(unpaid) if unpaid else 0
            notification_data['overdue_bills_count'] = overdue_count
            notification_data['unpaid_bills_count'] = unpaid_count
            notification_data['notification_count'] = overdue_count + unpaid_count
        except Exception:
            pass
        return notification_data

    @app.context_processor
    def inject_architecture_metadata():
        return {
            'canonical_domains': CANONICAL_DOMAINS,
            'platform_portals': PORTALS,
            'portal_modules': PORTAL_MODULES,
            'permission_catalog': PERMISSION_CATALOG,
            'user_types': USER_TYPES,
            'access_levels': ACCESS_LEVELS,
        }

    @app.template_filter('html_unescape')
    def html_unescape_filter(value):
        if value is None:
            return ''
        return html_unescape(str(value))

    @app.route('/health')
    def health():
        return {'status': 'ok'}, 200
