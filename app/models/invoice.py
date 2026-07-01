"""
Invoice Model - SQLAlchemy ORM
Frozen invoice snapshots generated from completed jobs.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import BaseModelMixin, TenantScopedMixin


class Invoice(db.Model, BaseModelMixin, TenantScopedMixin):
    __tablename__ = 'invoice'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'invoice_number', name='uq_invoice_tenant_number'),
        UniqueConstraint('job_id', name='uq_invoice_job'),
    )

    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_PAID = 'paid'
    STATUS_VOID = 'void'
    VALID_STATUSES = [STATUS_DRAFT, STATUS_SENT, STATUS_PAID, STATUS_VOID]

    invoice_id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey('tenant.tenant_id', onupdate='CASCADE'), nullable=True, index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey('job.job_id', onupdate='CASCADE'), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey('customer.customer_id', onupdate='CASCADE'), nullable=False, index=True)

    # Compatibility columns kept for the live schema and legacy views.
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False, default='')

    # Frozen snapshot columns for invoices generated from completed jobs.
    customer_name_snapshot: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    customer_email_snapshot: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    vehicle_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    line_items_snapshot: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    notes_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default='ZAR')
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    pdf_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    email_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    line_items_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    email_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    job: Mapped["Job"] = relationship("Job", back_populates="invoice")
    customer: Mapped["Customer"] = relationship("Customer")

    @classmethod
    def generate_invoice_number(cls, tenant_id: Optional[int], job_id: int) -> str:
        prefix = f"T{tenant_id}" if tenant_id else "GEN"
        return f"INV-{prefix}-{job_id:06d}"

    @property
    def line_items(self) -> List[Dict[str, Any]]:
        items = self.line_items_json or self.line_items_snapshot or []
        return list(items) if isinstance(items, list) else []

    @property
    def customer_name_display(self) -> str:
        if self.customer_name:
            return self.customer_name
        if self.customer_name_snapshot:
            return self.customer_name_snapshot
        return self.customer.full_name if self.customer else ''

    @property
    def customer_email_display(self) -> str:
        if self.customer_email:
            return self.customer_email
        if self.customer_email_snapshot:
            return self.customer_email_snapshot
        return self.customer.email if self.customer else ''

    @property
    def is_overdue(self) -> bool:
        return self.status in {self.STATUS_DRAFT, self.STATUS_SENT} and self.due_date < date.today()

    def _ensure_status_transition(self, new_status: str, allowed_from: set[str], action: str) -> None:
        current_status = self.status or self.STATUS_DRAFT
        if current_status not in allowed_from:
            raise ValueError(f'Cannot {action} invoice from status {current_status}')
        if new_status not in self.VALID_STATUSES:
            raise ValueError(f'Unsupported invoice status: {new_status}')

    def mark_sent(self, *, email_subject: Optional[str] = None, email_body: Optional[str] = None) -> None:
        self._ensure_status_transition(self.STATUS_SENT, {self.STATUS_DRAFT}, 'send')
        self.status = self.STATUS_SENT
        self.sent_at = datetime.utcnow()
        self.delivery_status = 'sent'
        self.is_email_enabled = True
        self.is_email_sent = True
        if email_subject is not None:
            self.email_subject = email_subject
        if email_body is not None:
            self.email_body = email_body

    def mark_available(self) -> None:
        '''Mark the invoice as available in the customer portal.'''
        self._ensure_status_transition(self.STATUS_SENT, {self.STATUS_DRAFT, self.STATUS_SENT}, 'mark available')
        self.status = self.STATUS_SENT
        self.sent_at = datetime.utcnow()
        self.delivery_status = 'available'
        self.is_email_enabled = False
        self.is_email_sent = False

    def mark_paid(self) -> None:
        self._ensure_status_transition(self.STATUS_PAID, {self.STATUS_DRAFT, self.STATUS_SENT}, 'mark paid')
        self.status = self.STATUS_PAID
        self.paid_at = datetime.utcnow()
        self.delivery_status = 'paid'

    def mark_void(self) -> None:
        self._ensure_status_transition(self.STATUS_VOID, {self.STATUS_DRAFT, self.STATUS_SENT}, 'void')
        self.status = self.STATUS_VOID
        self.delivery_status = 'void'

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data['subtotal'] = float(self.subtotal or 0)
        data['tax_rate'] = float(self.tax_rate or 0)
        data['tax_amount'] = float(self.tax_amount or 0)
        data['total_amount'] = float(self.total_amount or 0)
        data['line_items'] = self.line_items
        data['customer_name_display'] = self.customer_name_display
        data['customer_email_display'] = self.customer_email_display
        data['is_overdue'] = self.is_overdue
        if self.due_date:
            data['due_date'] = self.due_date.isoformat()
        if self.issued_at:
            data['issued_at'] = self.issued_at.isoformat()
        if self.sent_at:
            data['sent_at'] = self.sent_at.isoformat()
        if self.paid_at:
            data['paid_at'] = self.paid_at.isoformat()
        return data


from app.models.customer import Customer
from app.models.job import Job
