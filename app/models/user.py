"""
User Model - SQLAlchemy ORM
Authentication and authorization with multi-tenant support
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import os
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Integer, inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions import db
from app.models.base import BaseModelMixin, TimestampMixin

# Role-permission mapping for the multi-tenant RBAC system
ROLE_PERMISSIONS = {
    'owner': [
        'manage_org', 'manage_users', 'manage_catalog', 'manage_inventory',
        'manage_jobs', 'manage_customers', 'manage_billing', 'view_reports'
    ],
    'admin': [
        'manage_users', 'manage_catalog', 'manage_inventory',
        'manage_jobs', 'manage_customers', 'manage_billing', 'view_reports'
    ],
    'manager': ['manage_jobs', 'manage_customers', 'manage_billing', 'view_reports'],
    'technician': ['manage_jobs', 'view_reports'],
    'parts_clerk': ['manage_catalog', 'manage_inventory', 'view_reports'],
    'viewer': ['view_reports'],
}

VALID_ROLES = list(ROLE_PERMISSIONS.keys())


class User(db.Model, BaseModelMixin, TimestampMixin):
    """User model for authentication"""

    __tablename__ = 'user'

    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, nullable=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    preferred_tenant_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('tenant.tenant_id'), nullable=True, index=True
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('customer.customer_id'), nullable=True, index=True
    )

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Legacy role column kept for backward compatibility during migration
    role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    # Supabase Auth integration fields
    supabase_user_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)

    # Relationships
    memberships: Mapped[List["TenantMembership"]] = relationship(
        "TenantMembership",
        back_populates="user",
        foreign_keys="TenantMembership.user_id",
        lazy="dynamic"
    )
    preferred_tenant: Mapped[Optional["Tenant"]] = relationship(
        "Tenant", foreign_keys=[preferred_tenant_id], lazy="select"
    )
    customer_profile: Mapped[Optional["Customer"]] = relationship(
        "Customer", foreign_keys=[customer_id], lazy="select"
    )


    @classmethod
    def _has_column(cls, column_name: str) -> bool:
        """Return True when the mapped table currently exposes a given column."""
        try:
            inspector = sa_inspect(db.engine)
            return any(col.get('name') == column_name for col in inspector.get_columns(cls.__tablename__))
        except Exception:
            return False

    @property
    def is_admin(self) -> bool:
        """Check if user is a superadmin"""
        return self.is_superadmin

    def get_tenants(self) -> List[Dict[str, Any]]:
        """Get all tenants this user belongs to"""
        from app.models.tenant_membership import TenantMembership
        memberships = db.session.execute(
            db.select(TenantMembership).where(
                db.and_(
                    TenantMembership.user_id == self.user_id,
                    TenantMembership.status == 'active'
                )
            )
        ).scalars().all()
        result = []
        for m in memberships:
            result.append({
                'tenant_id': m.tenant_id,
                'tenant_name': m.tenant.name if m.tenant else None,
                'tenant_slug': m.tenant.slug if m.tenant else None,
                'role': m.role,
                'is_default': m.is_default,
            })
        return result

    def get_role_in_tenant(self, tenant_id: int) -> Optional[str]:
        """Get user's role in a specific tenant"""
        if self.is_superadmin:
            return 'owner'
        from app.models.tenant_membership import TenantMembership
        membership = db.session.execute(
            db.select(TenantMembership).where(
                db.and_(
                    TenantMembership.user_id == self.user_id,
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.status == 'active'
                )
            )
        ).scalar_one_or_none()
        return membership.role if membership else None

    def has_permission(self, tenant_id: int, permission: str) -> bool:
        """Check if user has a specific permission in a tenant"""
        if self.is_superadmin:
            return True
        role = self.get_role_in_tenant(tenant_id)
        if not role:
            return False
        return permission in ROLE_PERMISSIONS.get(role, [])

    def get_default_tenant_id(self) -> Optional[int]:
        """Get the user's default tenant ID"""
        if self.preferred_tenant_id:
            return self.preferred_tenant_id
        from app.models.tenant_membership import TenantMembership
        # First try to find a default membership
        membership = db.session.execute(
            db.select(TenantMembership).where(
                db.and_(
                    TenantMembership.user_id == self.user_id,
                    TenantMembership.status == 'active',
                    TenantMembership.is_default == True
                )
            )
        ).scalar_one_or_none()
        if membership:
            return membership.tenant_id
        # Otherwise return the first active membership
        membership = db.session.execute(
            db.select(TenantMembership).where(
                db.and_(
                    TenantMembership.user_id == self.user_id,
                    TenantMembership.status == 'active'
                )
            ).order_by(TenantMembership.id)
        ).scalars().first()
        return membership.tenant_id if membership else None

    @classmethod
    def find_by_username(cls, username: str) -> Optional['User']:
        """Find user by username"""
        query = db.select(cls).where(cls.username == username)
        return db.session.execute(query).scalar_one_or_none()

    @classmethod
    def find_by_email(cls, email: str) -> Optional['User']:
        """Find user by email"""
        query = db.select(cls).where(cls.email == email)
        return db.session.execute(query).scalar_one_or_none()

    @classmethod
    def find_by_neon_auth_id(cls, supabase_user_id: str) -> Optional['User']:
        """Find user by Supabase Auth user ID."""
        # Ensure str type — auth payloads may return UUID objects which cause
        # "operator does not exist: character varying = uuid" on PostgreSQL.
        supabase_user_id = str(supabase_user_id)
        if not cls._has_column('supabase_user_id'):
            return None
        query = db.select(cls).where(cls.supabase_user_id == supabase_user_id)
        return db.session.execute(query).scalar_one_or_none()



    @classmethod
    def authenticate_with_jwt(cls, jwt_payload: dict) -> Optional['User']:
        """Authenticate user with Supabase Auth JWT payload."""
        supabase_user_id = jwt_payload.get('sub')
        if not supabase_user_id:
            return None
        supabase_user_id = str(supabase_user_id)

        email = (jwt_payload.get('email') or '').strip().lower() or None
        name = (jwt_payload.get('name') or '').strip()
        logger = logging.getLogger(__name__)

        bootstrap_email = (os.environ.get('SUPERADMIN_EMAIL') or '').strip().lower()
        is_bootstrap_superadmin = bool(email and bootstrap_email and email == bootstrap_email)

        def _mark_superadmin(account: 'User') -> None:
            account.is_superadmin = True
            account.role = 'platform_owner'
            account.preferred_tenant_id = None
            account.customer_id = None
            account.email_verified = True

        try:
            user = cls.find_by_neon_auth_id(supabase_user_id)
            if user:
                if not user.is_active:
                    return None
                if is_bootstrap_superadmin and not _user_has_tenant_links(user):
                    _mark_superadmin(user)
                user.last_login = datetime.utcnow()
                db.session.commit()
                fresh_user = cls.find_by_id(user.user_id)
                return fresh_user or user

            if email:
                existing = cls.find_by_email(email)
                if existing:
                    if cls._has_column('supabase_user_id'):
                        existing.supabase_user_id = supabase_user_id
                    if is_bootstrap_superadmin and not _user_has_tenant_links(existing):
                        _mark_superadmin(existing)
                    else:
                        existing.email_verified = True
                    existing.last_login = datetime.utcnow()
                    db.session.commit()
                    fresh_existing = cls.find_by_id(existing.user_id)
                    return fresh_existing or existing

            username = email.split('@')[0] if email else f"user_{supabase_user_id[:8]}"
            base_username = username
            counter = 1
            while cls.find_by_username(username):
                username = f"{base_username}{counter}"
                counter += 1

            user_kwargs = dict(
                username=username,
                email=email,
                is_active=True,
                email_verified=True,
            )
            if is_bootstrap_superadmin:
                user_kwargs['is_superadmin'] = True
                user_kwargs['role'] = 'platform_owner'
                user_kwargs['preferred_tenant_id'] = None
                user_kwargs['customer_id'] = None
            if cls._has_column('supabase_user_id'):
                user_kwargs['supabase_user_id'] = supabase_user_id
            user = cls(**user_kwargs)
            user.last_login = datetime.utcnow()
            if name and not getattr(user, 'full_name', None):
                # Keep the payload available for future migrations even if the
                # current schema does not expose a dedicated full_name column.
                pass
            db.session.add(user)
            db.session.commit()
            fresh_user = cls.find_by_id(user.user_id)
            return fresh_user or user
        except Exception as exc:
            logger.error(f"Supabase JWT bootstrap failed: {exc}")
            try:
                db.session.rollback()
            except Exception:
                pass

            # If another concurrent request already created the row, re-use it.
            try:
                if email:
                    existing = cls.find_by_email(email)
                    if existing and existing.is_active:
                        if cls._has_column('supabase_user_id'):
                            existing.supabase_user_id = supabase_user_id
                        if is_bootstrap_superadmin and not _user_has_tenant_links(existing):
                            _mark_superadmin(existing)
                        else:
                            existing.email_verified = True
                        existing.last_login = datetime.utcnow()
                        db.session.commit()
                        fresh_existing = cls.find_by_id(existing.user_id)
                        return fresh_existing or existing
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
            return None

    def update_last_login(self) -> bool:
        """Update the last login timestamp"""
        self.last_login = datetime.utcnow()
        db.session.commit()
        return True

    def deactivate(self) -> bool:
        """Deactivate user account"""
        self.is_active = False
        db.session.commit()
        return True

    def activate(self) -> bool:
        """Activate user account"""
        self.is_active = True
        db.session.commit()
        return True

    @classmethod
    def get_by_role(cls, role: str) -> List['User']:
        """Get all users with a specific legacy role"""
        query = db.select(cls).where(db.and_(cls.role == role, cls.is_active == True))
        return list(db.session.execute(query).scalars())

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Convert user to dictionary"""
        data = {
            'user_id': self.user_id,
            'username': self.username,
            'email': self.email,
            'is_superadmin': self.is_superadmin,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'preferred_tenant_id': self.preferred_tenant_id,
            'customer_id': self.customer_id,
        }

        if include_sensitive:
            data['updated_at'] = self.updated_at.isoformat() if self.updated_at else None
            data['supabase_user_id'] = getattr(self, 'supabase_user_id', None)

        return data

    def __repr__(self) -> str:
        return f"<User {self.username}>"


def _user_has_tenant_links(user: 'User') -> bool:
    """Return True when a user is linked to any tenant/customer data."""
    try:
        if getattr(user, 'customer_id', None) or getattr(user, 'preferred_tenant_id', None):
            return True
        from app.models.tenant_membership import TenantMembership
        memberships = db.session.execute(
            db.select(TenantMembership).where(TenantMembership.user_id == user.user_id)
        ).scalars().all()
        return len(memberships) > 0
    except Exception:
        return True
