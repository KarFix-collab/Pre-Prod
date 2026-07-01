"""
Job Model - SQLAlchemy ORM
Work orders with services and parts, multi-tenant scoped
"""
from typing import List, Optional, Tuple
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Date, Numeric, Boolean, Integer, ForeignKey, and_, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property
from app.extensions import db
from app.models.base import BaseModelMixin, TenantScopedMixin


class JobService(db.Model):
    """Junction table for Job-Service relationship"""

    __tablename__ = 'job_service'

    job_id: Mapped[int] = mapped_column(ForeignKey('job.job_id', onupdate='CASCADE'), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey('service.service_id', onupdate='CASCADE'), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    line_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="job_services")
    service: Mapped["Service"] = relationship("Service", back_populates="job_services")

    @property
    def total_cost(self) -> Decimal:
        """Calculate total cost for this service entry"""
        if self.line_total is not None:
            return Decimal(str(self.line_total))
        unit_cost = self.unit_cost if self.unit_cost is not None else self.service.cost
        return Decimal(str(unit_cost)) * Decimal(str(self.qty))


class JobPart(db.Model):
    """Junction table for Job-Part relationship"""

    __tablename__ = 'job_part'

    job_id: Mapped[int] = mapped_column(ForeignKey('job.job_id', onupdate='CASCADE'), primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey('part.part_id', onupdate='CASCADE'), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    line_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="job_parts")
    part: Mapped["Part"] = relationship("Part", back_populates="job_parts")

    @property
    def total_cost(self) -> Decimal:
        """Calculate total cost for this part entry"""
        if self.line_total is not None:
            return Decimal(str(self.line_total))
        unit_cost = self.unit_cost if self.unit_cost is not None else self.part.cost
        return Decimal(str(unit_cost)) * Decimal(str(self.qty))


class JobStatusHistory(db.Model, BaseModelMixin, TenantScopedMixin):
    """Immutable audit trail of job status transitions."""

    __tablename__ = 'job_status_history'

    history_id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('tenant.tenant_id'), nullable=True, index=True
    )
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('job.job_id', onupdate='CASCADE'), nullable=False, index=True
    )
    old_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    changed_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('user.user_id', onupdate='CASCADE'), nullable=True, index=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship("Job", back_populates="status_history")
    changed_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[changed_by_user_id])


class JobAttachment(db.Model, BaseModelMixin, TenantScopedMixin):
    """Lightweight metadata for job attachments and photos."""

    __tablename__ = 'job_attachment'

    attachment_id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('tenant.tenant_id'), nullable=True, index=True
    )
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('job.job_id', onupdate='CASCADE'), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('user.user_id', onupdate='CASCADE'), nullable=True, index=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="attachments")
    uploaded_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by_user_id])


class Job(db.Model, BaseModelMixin, TenantScopedMixin):
    """Job (Work Order) model class"""

    __tablename__ = 'job'

    STATUS_DRAFT = 'draft'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_AWAITING_PARTS = 'awaiting_parts'
    STATUS_COMPLETED = 'completed'
    STATUS_DELIVERED = 'delivered'
    STATUS_CANCELLED = 'cancelled'
    STATUS_REJECTED = 'rejected'
    VALID_STATUSES = [
        STATUS_DRAFT,
        STATUS_IN_PROGRESS,
        STATUS_AWAITING_PARTS,
        STATUS_COMPLETED,
        STATUS_DELIVERED,
        STATUS_CANCELLED,
        STATUS_REJECTED,
    ]
    STATUS_LABELS = {
        STATUS_DRAFT: 'Draft',
        STATUS_IN_PROGRESS: 'In Progress',
        STATUS_AWAITING_PARTS: 'Awaiting Parts',
        STATUS_COMPLETED: 'Completed',
        STATUS_DELIVERED: 'Delivered',
        STATUS_CANCELLED: 'Cancelled',
        STATUS_REJECTED: 'Rejected',
    }

    job_id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('tenant.tenant_id'), nullable=True, index=True
    )
    job_date: Mapped[date] = mapped_column(Date, nullable=False)
    customer: Mapped[int] = mapped_column(ForeignKey('customer.customer_id', onupdate='CASCADE'), nullable=False)
    vehicle_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('vehicle.vehicle_id', onupdate='CASCADE'), nullable=True, index=True
    )
    mileage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT, index=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('user.user_id'), nullable=True
    )

    # Relationships
    customer_rel: Mapped["Customer"] = relationship("Customer", back_populates="jobs")
    vehicle_rel: Mapped[Optional["Vehicle"]] = relationship("Vehicle", back_populates="jobs")
    job_services: Mapped[List["JobService"]] = relationship("JobService", back_populates="job", cascade="all, delete-orphan")
    job_parts: Mapped[List["JobPart"]] = relationship("JobPart", back_populates="job", cascade="all, delete-orphan")
    invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", back_populates="job", uselist=False)
    assignee: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_to])
    tenant: Mapped[Optional["Tenant"]] = relationship("Tenant", backref="jobs")
    status_history: Mapped[List["JobStatusHistory"]] = relationship(
        "JobStatusHistory",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStatusHistory.history_id.asc()",
    )
    attachments: Mapped[List["JobAttachment"]] = relationship(
        "JobAttachment",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobAttachment.attachment_id.asc()",
    )

    def _record_status_history(self, previous_status: Optional[str], new_status: str, changed_by_user_id: Optional[int] = None, note: Optional[str] = None) -> None:
        """Create an immutable history entry for a status transition."""
        history = JobStatusHistory(
            tenant_id=self.tenant_id,
            job_id=self.job_id,
            old_status=previous_status,
            new_status=new_status,
            changed_by_user_id=changed_by_user_id,
            note=note,
        )
        db.session.add(history)

    def set_status(self, new_status: str, changed_by_user_id: Optional[int] = None, note: Optional[str] = None, initial: bool = False) -> bool:
        """Update the workflow state and record the transition."""
        if not new_status or new_status not in self.VALID_STATUSES:
            raise ValueError(f"Unsupported job status: {new_status}")

        previous_status = getattr(self, 'status', None)
        self.status = new_status
        if new_status in (self.STATUS_COMPLETED, self.STATUS_DELIVERED, self.STATUS_CANCELLED, self.STATUS_REJECTED):
            self.completed = True
        elif new_status in (self.STATUS_DRAFT, self.STATUS_IN_PROGRESS, self.STATUS_AWAITING_PARTS):
            self.completed = False

        if initial or previous_status != new_status:
            self._record_status_history(previous_status, new_status, changed_by_user_id=changed_by_user_id, note=note)
        return True

    def assign_technician(self, technician_user_id: Optional[int], changed_by_user_id: Optional[int] = None, note: Optional[str] = None) -> bool:
        """Assign or reassign the job to a technician."""
        previous_assignee = self.assigned_to
        self.assigned_to = technician_user_id
        if previous_assignee != technician_user_id:
            self._record_status_history(self.status, self.status, changed_by_user_id=changed_by_user_id, note=note or f"Assigned technician changed from {previous_assignee} to {technician_user_id}")
        return True

    def add_internal_note(self, note: str, changed_by_user_id: Optional[int] = None) -> bool:
        """Append an internal note to the work order."""
        note = (note or '').strip()
        if not note:
            raise ValueError('Note cannot be empty')
        if self.internal_notes:
            self.internal_notes = f"{self.internal_notes}\n---\n{note}"
        else:
            self.internal_notes = note
        self._record_status_history(self.status, self.status, changed_by_user_id=changed_by_user_id, note=note)
        return True

    @property
    def assigned_technician_name(self) -> str:
        """Friendly name for the assigned technician."""
        return self.assignee.username if self.assignee else ''


    @classmethod
    def get_current_jobs(cls, page: int = 1, per_page: int = 10) -> Tuple[List['Job'], int]:
        """Get current incomplete jobs with pagination, scoped to tenant"""
        from app.models.customer import Customer

        base_filter = [cls.completed == False]
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            base_filter.append(cls.tenant_id == tenant_id)

        total = db.session.execute(
            db.select(db.func.count()).select_from(cls).where(and_(*base_filter))
        ).scalar() or 0

        offset = (page - 1) * per_page
        query = (
            db.select(cls)
            .where(and_(*base_filter))
            .join(Customer, cls.customer == Customer.customer_id)
            .order_by(Customer.first_name, Customer.family_name, cls.job_date.desc())
            .offset(offset)
            .limit(per_page)
        )

        jobs = list(db.session.execute(query).scalars())
        return jobs, total

    @classmethod
    def get_all_with_customer_info(cls) -> List['Job']:
        """Get all jobs with customer information loaded, scoped to tenant"""
        from app.models.customer import Customer
        query = db.select(cls).join(Customer)
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            query = query.where(cls.tenant_id == tenant_id)
        query = query.order_by(cls.job_date.desc())
        return list(db.session.execute(query).scalars())

    @classmethod
    def get_unpaid_jobs(cls, customer_name: Optional[str] = None) -> List['Job']:
        """Get unpaid jobs, optionally filtered by customer name"""
        from app.models.customer import Customer

        filters = [cls.paid == False, cls.completed == True]
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            filters.append(cls.tenant_id == tenant_id)

        query = db.select(cls).join(Customer).where(and_(*filters))

        if customer_name and customer_name != 'Choose...':
            full_name_expr = db.func.concat(
                db.func.coalesce(Customer.first_name, ''), ' ', Customer.family_name
            )
            query = query.where(full_name_expr == customer_name)

        query = query.order_by(Customer.family_name, Customer.first_name, cls.job_date)
        return list(db.session.execute(query).scalars())

    @classmethod
    def get_overdue_jobs(cls, days_threshold: int = 14) -> List['Job']:
        """Get overdue jobs (unpaid completed jobs with overdue invoices, or legacy job-date fallback)."""
        from app.models.customer import Customer
        from app.models.invoice import Invoice
        import datetime as dt

        threshold_date = date.today() - dt.timedelta(days=days_threshold)
        filters = [cls.paid == False, cls.completed == True]
        tenant_id = cls._get_current_tenant_id()
        if tenant_id:
            filters.append(cls.tenant_id == tenant_id)

        query = (
            db.select(cls)
            .join(Customer)
            .outerjoin(Invoice, Invoice.job_id == cls.job_id)
            .where(and_(*filters))
            .order_by(cls.job_date.asc())
        )

        jobs = list(db.session.execute(query).scalars())
        overdue_jobs: List['Job'] = []
        for job in jobs:
            invoice = getattr(job, 'invoice', None)
            if invoice and invoice.due_date:
                if invoice.status != Invoice.STATUS_PAID and invoice.due_date < date.today():
                    overdue_jobs.append(job)
            elif job.job_date < threshold_date:
                overdue_jobs.append(job)
        return overdue_jobs

    def get_services(self) -> List[dict]:
        """Get services for this job"""
        return [
            {
                'job_service_id': js.job_id,
                'service_id': js.service_id,
                'service_name': js.service.service_name,
                'qty': js.qty,
                'cost': float(js.unit_cost if js.unit_cost is not None else js.service.cost),
                'total_cost': float(js.total_cost)
            }
            for js in self.job_services
        ]

    def get_parts(self) -> List[dict]:
        """Get parts for this job"""
        return [
            {
                'job_part_id': jp.job_id,
                'part_id': jp.part_id,
                'part_name': jp.part.part_name,
                'qty': jp.qty,
                'cost': float(jp.unit_cost if jp.unit_cost is not None else jp.part.cost),
                'total_cost': float(jp.total_cost)
            }
            for jp in self.job_parts
        ]

    def _find_job_service(self, service_id: int) -> Optional[JobService]:
        for job_service in self.job_services:
            if job_service.service_id == service_id:
                return job_service
        return None

    def _find_job_part(self, part_id: int) -> Optional[JobPart]:
        for job_part in self.job_parts:
            if job_part.part_id == part_id:
                return job_part
        return None

    def add_service(self, service_id: int, quantity: int) -> bool:
        """Add a service to this job"""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        from app.models.service import Service
        service = Service.find_by_id(service_id)
        if not service:
            raise ValueError(f"Service {service_id} not found")

        existing = self._find_job_service(service_id)
        if existing:
            existing.qty += quantity
            existing.unit_cost = service.cost
            existing.line_total = service.cost * Decimal(str(existing.qty))
        else:
            job_service = JobService(job_id=self.job_id, service_id=service_id, qty=quantity, unit_cost=service.cost, line_total=service.cost * Decimal(str(quantity)))
            db.session.add(job_service)
        self._update_total_cost()
        db.session.commit()
        return True

    def update_service_quantity(self, service_id: int, quantity: int) -> bool:
        """Update a service quantity or remove it when quantity is zero."""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        existing = self._find_job_service(service_id)
        if not existing:
            raise ValueError(f"Service {service_id} not found on this job")

        if quantity <= 0:
            db.session.delete(existing)
        else:
            existing.qty = quantity
            existing.unit_cost = existing.service.cost
            existing.line_total = existing.unit_cost * Decimal(str(quantity))
        self._update_total_cost()
        db.session.commit()
        return True

    def remove_service(self, service_id: int) -> bool:
        """Remove a service from this job"""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        existing = self._find_job_service(service_id)
        if not existing:
            raise ValueError(f"Service {service_id} not found on this job")

        db.session.delete(existing)
        self._update_total_cost()
        db.session.commit()
        return True

    def add_part(self, part_id: int, quantity: int) -> bool:
        """Add a part to this job"""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        from app.models.part import Part
        part = Part.find_by_id(part_id)
        if not part:
            raise ValueError(f"Part {part_id} not found")

        existing = self._find_job_part(part_id)
        if existing:
            existing.qty += quantity
            existing.unit_cost = part.cost
            existing.line_total = part.cost * Decimal(str(existing.qty))
        else:
            job_part = JobPart(job_id=self.job_id, part_id=part_id, qty=quantity, unit_cost=part.cost, line_total=part.cost * Decimal(str(quantity)))
            db.session.add(job_part)
        self._update_total_cost()
        db.session.commit()
        return True

    def update_part_quantity(self, part_id: int, quantity: int) -> bool:
        """Update a part quantity or remove it when quantity is zero."""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        existing = self._find_job_part(part_id)
        if not existing:
            raise ValueError(f"Part {part_id} not found on this job")

        if quantity <= 0:
            db.session.delete(existing)
        else:
            existing.qty = quantity
            existing.unit_cost = existing.part.cost
            existing.line_total = existing.unit_cost * Decimal(str(quantity))
        self._update_total_cost()
        db.session.commit()
        return True

    def remove_part(self, part_id: int) -> bool:
        """Remove a part from this job"""
        if self.completed:
            raise ValueError("Cannot modify a completed job")

        existing = self._find_job_part(part_id)
        if not existing:
            raise ValueError(f"Part {part_id} not found on this job")

        db.session.delete(existing)
        self._update_total_cost()
        db.session.commit()
        return True

    def sync_vehicle_mileage(self) -> bool:
        """Synchronize the assigned vehicle's current mileage from this job."""
        if not self.vehicle_rel or self.mileage is None:
            return False
        self.vehicle_rel.mileage = self.mileage
        return True

    def mark_as_completed(self) -> bool:
        """Mark job as completed"""
        self.set_status(self.STATUS_COMPLETED)
        self.sync_vehicle_mileage()
        self._update_total_cost()
        return True

    def mark_as_paid(self) -> bool:
        """Mark job as paid"""
        self.paid = True
        if getattr(self, 'invoice', None):
            self.invoice.mark_paid()
        return True

    def _update_total_cost(self) -> None:
        """Recalculate and update total cost"""
        service_total = sum(Decimal(str(js.total_cost)) for js in self.job_services)
        part_total = sum(Decimal(str(jp.total_cost)) for jp in self.job_parts)
        self.total_cost = service_total + part_total

    @hybrid_property
    def is_overdue(self) -> bool:
        """Check if job is overdue (14 days threshold)"""
        if self.paid or not self.completed:
            return False
        invoice = getattr(self, 'invoice', None)
        if invoice and invoice.due_date:
            return invoice.status != getattr(invoice.__class__, 'STATUS_PAID', 'paid') and invoice.due_date < date.today()
        if not self.job_date:
            return False
        days_diff = (date.today() - self.job_date).days
        return days_diff > 14

    @property
    def status_text(self) -> str:
        """Get a user-friendly workflow status label."""
        if self.paid and self.status in {self.STATUS_COMPLETED, self.STATUS_DELIVERED}:
            return f"{self.STATUS_LABELS.get(self.status, self.status.title())} & Paid"
        return self.STATUS_LABELS.get(self.status, self.status.replace('_', ' ').title())


    @property
    def vehicle_display_name(self) -> str:
        """Get display name for the assigned vehicle."""
        return self.vehicle_rel.display_name if self.vehicle_rel else ""

    @property
    def vehicle_registration_number(self) -> str:
        """Get registration number for the assigned vehicle."""
        return self.vehicle_rel.registration_number if self.vehicle_rel else ""

    @property
    def days_since_job(self) -> int:
        """Days since job was created"""
        if not self.job_date:
            return 0
        return (date.today() - self.job_date).days

    @property
    def days_overdue(self) -> int:
        """Days overdue for unpaid jobs; zero for paid or non-overdue jobs."""
        return self.days_since_job if self.is_overdue else 0

    @property
    def overdue(self) -> bool:
        """Compatibility-friendly alias for overdue status in templates."""
        return self.is_overdue


    @property
    def first_name(self) -> str:
        """Compatibility accessor for templates expecting dict-style job payloads."""
        return self.customer_rel.first_name if self.customer_rel else ""

    @property
    def family_name(self) -> str:
        """Compatibility accessor for templates expecting dict-style job payloads."""
        return self.customer_rel.family_name if self.customer_rel else ""

    @property
    def customer_id(self) -> int:
        """Compatibility accessor for templates expecting dict-style job payloads."""
        return self.customer


    def to_dict(self) -> dict:
        """Convert to dictionary with computed fields"""
        data = super().to_dict()
        data['is_overdue'] = self.is_overdue
        data['status_text'] = self.status_text
        data['status'] = self.status
        data['assigned_technician_name'] = self.assigned_technician_name
        data['internal_notes'] = self.internal_notes
        data['days_since_job'] = self.days_since_job
        if self.total_cost is not None:
            data['total_cost'] = float(self.total_cost)
        if self.customer_rel:
            data['first_name'] = self.customer_rel.first_name
            data['family_name'] = self.customer_rel.family_name
            data['customer_id'] = self.customer_rel.customer_id
        if self.vehicle_rel:
            data['vehicle_id'] = self.vehicle_rel.vehicle_id
            data['vehicle_display_name'] = self.vehicle_rel.display_name
            data['registration_number'] = self.vehicle_rel.registration_number
            data['vehicle_make'] = self.vehicle_rel.make
            data['vehicle_model'] = self.vehicle_rel.model
            data['vehicle_year'] = self.vehicle_rel.year
        data['attachment_count'] = len(self.attachments) if getattr(self, 'attachments', None) is not None else 0
        data['history_count'] = len(self.status_history) if getattr(self, 'status_history', None) is not None else 0
        if self.mileage is not None:
            data['mileage'] = self.mileage
        if getattr(self, 'invoice', None):
            data['invoice'] = self.invoice.to_dict()
        return data


# Import for type hints
from app.models.customer import Customer
from app.models.service import Service
from app.models.part import Part
from app.models.user import User
from app.models.invoice import Invoice
