"""
Views Module
Contains all routes and controller logic
"""
from .main import main_bp
from .superadmin import superadmin_bp
from .technician import technician_bp
from .administrator import administrator_bp
from .auth import auth_bp
from .billing import billing_bp
from .customer import customer_bp
from .platform import platform_bp

__all__ = ['main_bp', 'superadmin_bp', 'technician_bp', 'administrator_bp', 'auth_bp', 'billing_bp', 'customer_bp', 'platform_bp']
