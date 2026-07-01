"""
Models Package
SQLAlchemy ORM models for the Automotive Repair Management System
"""
from app.extensions import db
from app.models.customer import Customer
from app.models.job import Job, JobService, JobPart, JobStatusHistory, JobAttachment
from app.models.invoice import Invoice
from app.models.service import Service
from app.models.part import Part
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.vehicle import Vehicle
from app.models.tenant_membership import TenantMembership
from app.models.inventory import Inventory, InventoryTransaction
from app.models.subscription import Subscription

__all__ = [
    'db',
    'Customer',
    'Job',
    'JobService',
    'JobPart',
    'JobStatusHistory',
    'JobAttachment',
    'Invoice',
    'Service',
    'Part',
    'User',
    'AuditLog',
    'Tenant',
    'Vehicle',
    'TenantMembership',
    'Inventory',
    'InventoryTransaction',
    'Subscription',
]
