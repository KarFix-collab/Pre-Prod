"""Central audit service for KarFix."""
from __future__ import annotations

from typing import Any, Optional

from flask import request, session

from app.extensions import db
from app.models.audit_log import AuditLog


class AuditService:
    def record_event(
        self,
        action: str,
        entity_type: str,
        entity_id: Optional[int] = None,
        *,
        tenant_id: Optional[int] = None,
        old_values: Optional[dict[str, Any]] = None,
        new_values: Optional[dict[str, Any]] = None,
        user_id: Optional[int] = None,
    ) -> Optional[AuditLog]:
        try:
            audit = AuditLog(
                user_id=user_id if user_id is not None else session.get('user_id'),
                tenant_id=tenant_id if tenant_id is not None else session.get('current_tenant_id'),
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                old_values=old_values,
                new_values=new_values,
                ip_address=request.headers.get('X-Forwarded-For', request.remote_addr),
                user_agent=request.headers.get('User-Agent'),
            )
            db.session.add(audit)
            db.session.commit()
            return audit
        except Exception:
            db.session.rollback()
            return None


audit_service = AuditService()
