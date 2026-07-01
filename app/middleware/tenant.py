"""Tenant Context Middleware
Sets g.current_tenant_id from URL path slug, session, or header.
"""
from flask import g, request, session
from app.services.tenant_context_service import _tenant_context_service


def init_tenant_middleware(app):
    """Register tenant middleware with Flask app"""

    @app.before_request
    def set_tenant_context():
        """Set tenant context before each request."""
        _tenant_context_service.resolve_from_request()
        return None

    @app.context_processor
    def inject_tenant_context():
        """Make tenant context available in all templates"""
        ctx = _tenant_context_service.get_context()
        user_tenants = None
        user_id = session.get('user_id')
        if user_id and session.get('logged_in'):
            try:
                user_tenants = _tenant_context_service.get_user_tenants(user_id)
            except Exception:
                user_tenants = None

        currency_code = 'ZAR'
        currency_symbol = 'R'
        current_tenant = getattr(g, 'current_tenant', None)
        if current_tenant:
            currency_code = getattr(current_tenant, 'currency_code', currency_code)
            currency_symbol = getattr(current_tenant, 'currency_symbol', currency_symbol)

        def format_currency(value):
            try:
                amount = float(value or 0)
            except (TypeError, ValueError):
                amount = 0.0
            return f"{currency_symbol}{amount:,.2f}"

        return {
            'current_tenant': current_tenant,
            'current_tenant_id': getattr(ctx, 'tenant_id', None),
            'current_membership': getattr(ctx, 'membership', None),
            'current_tenant_context': ctx,
            'user_tenants': user_tenants,
            'current_currency_code': currency_code,
            'current_currency_symbol': currency_symbol,
            'format_currency': format_currency,
        }
