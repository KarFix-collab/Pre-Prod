"""Audit Log Model for platform-wide SuperAdmin actions."""
from typing import Optional
from sqlalchemy import Integer, String, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions import db
from app.models.base import BaseModelMixin, TimestampMixin


class AuditLog(db.Model, BaseModelMixin, TimestampMixin):
    __tablename__ = 'audit_log'

    audit_log_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey('user.user_id', onupdate='CASCADE'), nullable=True, index=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey('tenant.tenant_id', onupdate='CASCADE'), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    old_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[Optional['User']] = relationship('User', foreign_keys=[user_id])
    tenant: Mapped[Optional['Tenant']] = relationship('Tenant', foreign_keys=[tenant_id])

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"
