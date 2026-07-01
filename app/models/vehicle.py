"""
Vehicle Model - SQLAlchemy ORM
Multiple vehicles per customer with tenant isolation
"""
from typing import List, Optional
from sqlalchemy import String, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions import db
from app.models.base import BaseModelMixin, TenantScopedMixin, TimestampMixin


class Vehicle(db.Model, BaseModelMixin, TenantScopedMixin, TimestampMixin):
    """Customer vehicle model class"""

    def save(self) -> "Vehicle":
        """Save vehicle and ensure tenant_id is assigned from the active workshop."""
        return TenantScopedMixin.save(self)

    __tablename__ = 'vehicle'

    vehicle_id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('tenant.tenant_id'), nullable=True, index=True
    )
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('customer.customer_id', onupdate='CASCADE'), nullable=False, index=True
    )
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    registration_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    vin: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    mileage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    customer: Mapped["Customer"] = relationship("Customer", back_populates="vehicles")
    tenant: Mapped[Optional["Tenant"]] = relationship("Tenant", backref="vehicles")
    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="vehicle_rel", lazy="dynamic")

    @property
    def display_name(self) -> str:
        """Human-friendly vehicle label."""
        parts = []
        if self.registration_number:
            parts.append(self.registration_number)
        make_model = " ".join(part for part in [self.make, self.model] if part)
        if make_model:
            parts.append(make_model)
        if self.year:
            parts.append(str(self.year))
        return " - ".join(parts) if parts else f"Vehicle #{self.vehicle_id}"

    def get_jobs(self, completed_only: bool = False) -> List["Job"]:
        """Get all jobs associated with this vehicle."""
        from app.models.job import Job
        query = self.jobs
        if completed_only:
            query = query.filter(Job.completed == True)
        return query.order_by(Job.job_date.desc()).all()

    @classmethod
    def get_all_for_customer(cls, customer_id: int) -> List["Vehicle"]:
        """Get all vehicles for a customer, scoped to tenant."""
        query = db.select(cls).where(cls.customer_id == customer_id)
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            query = query.where(cls.tenant_id == tenant_id)
        query = query.order_by(cls.is_primary.desc(), cls.make, cls.model, cls.year.desc())
        return list(db.session.execute(query).scalars())

    @classmethod
    def find_by_customer_and_id(cls, customer_id: int, vehicle_id: int) -> Optional["Vehicle"]:
        """Find a vehicle belonging to a specific customer."""
        query = db.select(cls).where(
            cls.vehicle_id == vehicle_id,
            cls.customer_id == customer_id,
        )
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            query = query.where(cls.tenant_id == tenant_id)
        return db.session.execute(query).scalar_one_or_none()

    def to_dict(self) -> dict:
        """Convert to dictionary with computed fields."""
        data = super().to_dict()
        data['display_name'] = self.display_name
        data['job_count'] = len(self.get_jobs()) if self.jobs is not None else 0
        return data


# Import for type hints
