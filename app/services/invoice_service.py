"""
Invoice Service
Create frozen invoices from completed jobs and make them available in the customer portal.
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

from flask import g

from app.extensions import db
from app.models.invoice import Invoice
from app.models.job import Job
from app.models.tenant import Tenant


class InvoiceService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _current_tenant_id() -> Optional[int]:
        return getattr(g, 'current_tenant_id', None)

    @staticmethod
    def _currency_symbol(currency: str) -> str:
        return {
            'ZAR': 'R',
            'USD': '$',
            'EUR': '€',
            'GBP': '£',
        }.get((currency or '').upper(), '')

    def _tenant_tax_rate(self, tenant_id: Optional[int]) -> Decimal:
        if not tenant_id:
            return Decimal('0')
        tenant = Tenant.find_by_id(tenant_id)
        if not tenant or not isinstance(tenant.settings, dict):
            return Decimal('0')
        for key in ('tax_rate', 'sales_tax_rate', 'vat_rate'):
            raw = tenant.settings.get(key)
            if raw is None:
                continue
            try:
                value = Decimal(str(raw))
                if value > 1:
                    value = value / Decimal('100')
                return max(Decimal('0'), value)
            except Exception:
                continue
        return Decimal('0')

    @staticmethod
    def _job_vehicle_snapshot(job: Job) -> Optional[Dict[str, Any]]:
        vehicle = getattr(job, 'vehicle_rel', None)
        if not vehicle:
            return None
        return {
            'vehicle_id': vehicle.vehicle_id,
            'display_name': vehicle.display_name,
            'registration_number': vehicle.registration_number,
            'make': vehicle.make,
            'model': vehicle.model,
            'year': vehicle.year,
        }

    def _reconcile_invoice_record(self, invoice: Invoice) -> bool:
        """Backfill compatibility fields on an existing invoice row when possible."""
        changed = False

        if not invoice.customer_name and invoice.customer_name_snapshot:
            invoice.customer_name = invoice.customer_name_snapshot
            changed = True
        if not invoice.customer_name and invoice.customer:
            invoice.customer_name = invoice.customer.full_name
            changed = True

        if not invoice.customer_email and invoice.customer_email_snapshot:
            invoice.customer_email = invoice.customer_email_snapshot
            changed = True
        if not invoice.customer_email and invoice.customer:
            invoice.customer_email = invoice.customer.email
            changed = True

        if not invoice.customer_name_snapshot and invoice.customer_name:
            invoice.customer_name_snapshot = invoice.customer_name
            changed = True
        if not invoice.customer_email_snapshot and invoice.customer_email:
            invoice.customer_email_snapshot = invoice.customer_email
            changed = True

        if not invoice.line_items_snapshot and invoice.line_items_json:
            invoice.line_items_snapshot = list(invoice.line_items_json)
            changed = True
        if not invoice.line_items_json and invoice.line_items_snapshot:
            invoice.line_items_json = list(invoice.line_items_snapshot)
            changed = True

        if invoice.tax_rate is None:
            invoice.tax_rate = self._tenant_tax_rate(invoice.tenant_id)
            changed = True

        if not invoice.due_date:
            base_date = invoice.issued_at.date() if invoice.issued_at else date.today()
            invoice.due_date = base_date + timedelta(days=14)
            changed = True

        if invoice.is_email_sent and not invoice.is_email_enabled:
            invoice.is_email_enabled = True
            changed = True

        if changed:
            db.session.commit()
        return changed

    def build_invoice_snapshot(self, job: Job, due_days: int = 14) -> Dict[str, Any]:
        try:
            due_days = int(due_days)
        except (TypeError, ValueError):
            raise ValueError('Due days must be a whole number')
        if due_days < 1 or due_days > 365:
            raise ValueError('Due days must be between 1 and 365')

        services = job.get_services()
        parts = job.get_parts()
        line_items: List[Dict[str, Any]] = []
        subtotal = Decimal('0')

        for item in services:
            line_items.append({**item, 'type': 'service'})
            subtotal += Decimal(str(item.get('total_cost') or 0))
        for item in parts:
            line_items.append({**item, 'type': 'part'})
            subtotal += Decimal(str(item.get('total_cost') or 0))

        if not line_items:
            raise ValueError('Cannot create an invoice without billable services or parts')

        tax_rate = self._tenant_tax_rate(job.tenant_id)
        tax_amount = (subtotal * tax_rate).quantize(Decimal('0.01')) if subtotal else Decimal('0.00')
        total_amount = (subtotal + tax_amount).quantize(Decimal('0.01'))

        customer = job.customer_rel
        invoice_number = Invoice.generate_invoice_number(job.tenant_id, job.job_id)
        base_date = job.job_date or date.today()
        due_date = base_date + timedelta(days=due_days)
        name = customer.full_name if customer else ''
        email = customer.email if customer else ''
        issued_at = datetime.utcnow()

        return {
            'invoice_number': invoice_number,
            'customer_id': customer.customer_id if customer else job.customer,
            'customer_name': name,
            'customer_email': email,
            'customer_name_snapshot': name,
            'customer_email_snapshot': email,
            'vehicle_snapshot': self._job_vehicle_snapshot(job),
            'currency': 'ZAR',
            'subtotal': subtotal,
            'tax_rate': tax_rate,
            'tax_amount': tax_amount,
            'total_amount': total_amount,
            'issued_at': issued_at,
            'due_date': due_date,
            'line_items_json': line_items,
            'line_items_snapshot': list(line_items),
            'notes_snapshot': getattr(job, 'internal_notes', None),
            'email_subject': f'Invoice {invoice_number} from KarFix',
            'pdf_generated_at': None,
            'is_email_enabled': False,
            'is_email_sent': False,
            'delivery_status': 'pending',
        }

    def get_invoice_for_job(self, job_id: int) -> Optional[Invoice]:
        try:
            invoice = db.session.execute(db.select(Invoice).where(Invoice.job_id == job_id)).scalar_one_or_none()
            if invoice:
                self._reconcile_invoice_record(invoice)
            return invoice
        except Exception as exc:
            self.logger.error('Failed to load invoice for job %s: %s', job_id, exc)
            db.session.rollback()
            return None

    def create_invoice_for_job(self, job_id: int, *, due_days: int = 14, send_email: bool = False) -> Tuple[bool, List[str], Optional[Invoice]]:
        try:
            job = Job.find_by_id(job_id)
            if not job:
                return False, ['Job does not exist'], None
            if not job.completed:
                return False, ['Only completed jobs can be invoiced'], None

            existing = self.get_invoice_for_job(job_id)
            if existing:
                if existing.status != Invoice.STATUS_PAID and existing.delivery_status != 'available':
                    existing.mark_available()
                    db.session.commit()
                return True, [], existing

            snapshot = self.build_invoice_snapshot(job, due_days=due_days)
            invoice = Invoice(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                **snapshot,
                status=Invoice.STATUS_DRAFT,
            )
            db.session.add(invoice)
            invoice.mark_available()
            db.session.commit()

            invoice = Invoice.find_by_id(invoice.invoice_id)
            return True, [], invoice
        except Exception as exc:
            self.logger.error('Failed to create invoice for job %s: %s', job_id, exc)
            db.session.rollback()
            return False, ['System error, please try again'], None


    def get_invoices_for_tenant(self, tenant_id: Optional[int]) -> List[Invoice]:
        """Return invoices visible to a tenant, newest first."""
        try:
            query = db.select(Invoice)
            if tenant_id:
                query = query.where(Invoice.tenant_id == tenant_id)
            invoices = list(db.session.execute(query.order_by(Invoice.issued_at.desc(), Invoice.invoice_id.desc())).scalars().all())
            for invoice in invoices:
                self._reconcile_invoice_record(invoice)
            return invoices
        except Exception as exc:
            self.logger.error('Failed to list tenant invoices: %s', exc)
            db.session.rollback()
            return []

    def get_invoices_for_customer(self, customer_id: int) -> List[Invoice]:
        """Return invoices belonging to a customer, newest first."""
        try:
            query = db.select(Invoice).where(Invoice.customer_id == customer_id)
            invoices = list(db.session.execute(query.order_by(Invoice.issued_at.desc(), Invoice.invoice_id.desc())).scalars().all())
            for invoice in invoices:
                self._reconcile_invoice_record(invoice)
            return invoices
        except Exception as exc:
            self.logger.error('Failed to list customer invoices: %s', exc)
            db.session.rollback()
            return []

    def get_invoice_by_id(self, invoice_id: int) -> Optional[Invoice]:
        try:
            invoice = Invoice.find_by_id(invoice_id)
            if invoice:
                self._reconcile_invoice_record(invoice)
            return invoice
        except Exception as exc:
            self.logger.error('Failed to load invoice %s: %s', invoice_id, exc)
            db.session.rollback()
            return None

    def get_unpaid_invoices(self, customer_name: Optional[str] = None) -> List[Invoice]:
        """Return unpaid invoices, optionally filtered by customer name."""
        try:
            tenant_id = self._current_tenant_id()
            invoices = self.get_invoices_for_tenant(tenant_id)
            unpaid = [inv for inv in invoices if inv.status in {Invoice.STATUS_DRAFT, Invoice.STATUS_SENT}]
            if customer_name and customer_name != 'Choose...':
                target = customer_name.strip().lower()
                unpaid = [inv for inv in unpaid if (inv.customer_name_display or '').strip().lower() == target]
            return unpaid
        except Exception as exc:
            self.logger.error('Failed to list unpaid invoices: %s', exc)
            db.session.rollback()
            return []

    def get_overdue_invoices(self, days_threshold: int = 14, customer_name: Optional[str] = None) -> List[Invoice]:
        """Return overdue invoices, optionally filtered by customer name."""
        try:
            tenant_id = self._current_tenant_id()
            invoices = self.get_invoices_for_tenant(tenant_id)
            overdue = [inv for inv in invoices if inv.is_overdue]
            if customer_name and customer_name != 'Choose...':
                target = customer_name.strip().lower()
                overdue = [inv for inv in overdue if (inv.customer_name_display or '').strip().lower() == target]
            return overdue
        except Exception as exc:
            self.logger.error('Failed to list overdue invoices: %s', exc)
            db.session.rollback()
            return []

    def get_invoice_billing_statistics(self, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        """Summarize tenant invoice billing values for dashboards and APIs."""
        try:
            invoices = self.get_invoices_for_tenant(tenant_id if tenant_id is not None else self._current_tenant_id())
            paid = [inv for inv in invoices if inv.status == Invoice.STATUS_PAID]
            unpaid = [inv for inv in invoices if inv.status != Invoice.STATUS_PAID]
            overdue = [inv for inv in invoices if inv.is_overdue]

            total_amount = float(sum(Decimal(str(inv.total_amount or 0)) for inv in invoices))
            total_paid = float(sum(Decimal(str(inv.total_amount or 0)) for inv in paid))
            total_unpaid = float(sum(Decimal(str(inv.total_amount or 0)) for inv in unpaid))
            total_overdue = float(sum(Decimal(str(inv.total_amount or 0)) for inv in overdue))

            return {
                'total_bills': len(invoices),
                'total_invoices': len(invoices),
                'total_amount': total_amount,
                'total_revenue': total_amount,
                'total_paid': total_paid,
                'paid_amount': total_paid,
                'total_unpaid': total_unpaid,
                'unpaid_amount': total_unpaid,
                'total_overdue': total_overdue,
                'overdue_amount': total_overdue,
                'paid_bills': len(paid),
                'unpaid_bills': len(unpaid),
                'overdue_bills': len(overdue),
                'payment_rate': (total_paid / total_amount * 100) if total_amount > 0 else 0,
            }
        except Exception as exc:
            self.logger.error('Failed to summarize invoice billing statistics: %s', exc)
            db.session.rollback()
            return {
                'total_bills': 0,
                'total_invoices': 0,
                'total_amount': 0.0,
                'total_revenue': 0.0,
                'total_paid': 0.0,
                'paid_amount': 0.0,
                'total_unpaid': 0.0,
                'unpaid_amount': 0.0,
                'total_overdue': 0.0,
                'overdue_amount': 0.0,
                'paid_bills': 0,
                'unpaid_bills': 0,
                'overdue_bills': 0,
                'payment_rate': 0.0,
            }

    def get_customer_invoice_summary(self, customer_id: int) -> Dict[str, Any]:
        """Summarize invoice billing for a single customer."""
        try:
            from app.models.customer import Customer

            customer = Customer.find_by_id(customer_id)
            invoices = self.get_invoices_for_customer(customer_id)
            paid = [inv for inv in invoices if inv.status == Invoice.STATUS_PAID]
            unpaid = [inv for inv in invoices if inv.status != Invoice.STATUS_PAID]
            overdue = [inv for inv in invoices if inv.is_overdue]

            total_amount = float(sum(Decimal(str(inv.total_amount or 0)) for inv in invoices))
            paid_amount = float(sum(Decimal(str(inv.total_amount or 0)) for inv in paid))
            unpaid_amount = float(sum(Decimal(str(inv.total_amount or 0)) for inv in unpaid))
            overdue_amount = float(sum(Decimal(str(inv.total_amount or 0)) for inv in overdue))

            return {
                'customer_info': customer.to_dict() if customer else {},
                'total_jobs': len(invoices),
                'total_amount': total_amount,
                'paid_jobs': len(paid),
                'paid_amount': paid_amount,
                'unpaid_jobs': len(unpaid),
                'unpaid_amount': unpaid_amount,
                'total_unpaid_amount': unpaid_amount,
                'overdue_jobs': len(overdue),
                'overdue_amount': overdue_amount,
                'payment_rate': (paid_amount / total_amount * 100) if total_amount > 0 else 0,
            }
        except Exception as exc:
            self.logger.error('Failed to summarize customer invoice billing: %s', exc)
            db.session.rollback()
            return {
                'customer_info': {},
                'total_jobs': 0,
                'total_amount': 0.0,
                'paid_jobs': 0,
                'paid_amount': 0.0,
                'unpaid_jobs': 0,
                'unpaid_amount': 0.0,
                'total_unpaid_amount': 0.0,
                'overdue_jobs': 0,
                'overdue_amount': 0.0,
                'payment_rate': 0.0,
            }

    def _smtp_settings(self) -> Dict[str, Any]:
        return {
            'host': os.getenv('MAIL_HOST') or os.getenv('SMTP_HOST') or os.getenv('MAIL_SERVER'),
            'port': int(os.getenv('MAIL_PORT') or os.getenv('SMTP_PORT') or '587'),
            'username': os.getenv('MAIL_USERNAME') or os.getenv('SMTP_USERNAME'),
            'password': os.getenv('MAIL_PASSWORD') or os.getenv('SMTP_PASSWORD'),
            'use_tls': (os.getenv('MAIL_USE_TLS') or os.getenv('SMTP_USE_TLS') or 'true').lower() == 'true',
            'use_ssl': (os.getenv('MAIL_USE_SSL') or os.getenv('SMTP_USE_SSL') or 'false').lower() == 'true',
            'from_email': os.getenv('MAIL_FROM') or os.getenv('SMTP_FROM') or os.getenv('MAIL_DEFAULT_SENDER') or os.getenv('MAIL_USERNAME') or os.getenv('SMTP_USERNAME'),
            'from_name': os.getenv('MAIL_FROM_NAME') or 'KarFix Invoicing',
        }

    def _render_email(self, invoice: Invoice) -> Tuple[str, str]:
        symbol = self._currency_symbol(invoice.currency)
        rows = []
        for item in invoice.line_items:
            name = item.get('service_name') or item.get('part_name') or 'Line item'
            qty = item.get('qty', 1)
            unit = item.get('cost', 0)
            total = item.get('total_cost', 0)
            rows.append(
                f'<tr><td>{name}</td><td>{qty}</td><td>{symbol}{float(unit):,.2f}</td><td>{symbol}{float(total):,.2f}</td></tr>'
            )
        items_html = '\n'.join(rows) or '<tr><td colspan="4">No line items</td></tr>'
        subject = invoice.email_subject or f'Invoice {invoice.invoice_number} from KarFix'
        recipient_name = invoice.customer_name_display or 'Customer'
        due_date = invoice.due_date.isoformat() if invoice.due_date else 'N/A'
        body = f"""<html><body>
        <h2>{subject}</h2>
        <p>Dear {recipient_name},</p>
        <p>Your vehicle repair invoice is ready.</p>
        <p><strong>Invoice:</strong> {invoice.invoice_number}<br>
           <strong>Due date:</strong> {due_date}<br>
           <strong>Amount due:</strong> {symbol}{float(invoice.total_amount):,.2f}</p>
        <table border="1" cellspacing="0" cellpadding="6">
        <thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead>
        <tbody>{items_html}</tbody></table>
        <p><strong>Subtotal:</strong> {symbol}{float(invoice.subtotal):,.2f}<br>
           <strong>Tax:</strong> {symbol}{float(invoice.tax_amount):,.2f}<br>
           <strong>Total:</strong> {symbol}{float(invoice.total_amount):,.2f}</p>
        </body></html>"""
        text = (
            f"Invoice {invoice.invoice_number}\n"
            f"Customer: {recipient_name}\n"
            f"Due date: {due_date}\n"
            f"Total due: {symbol}{float(invoice.total_amount):,.2f}\n"
        )
        return subject, body + '\n<!--TEXT:' + text.replace('--', '-') + '-->'

    def send_invoice_email(self, invoice_id: int) -> Tuple[bool, List[str]]:
        '''Compatibility shim for portal-only deployments.'''
        try:
            invoice = Invoice.find_by_id(invoice_id)
            if not invoice:
                return False, ['Invoice does not exist']

            self._reconcile_invoice_record(invoice)
            if invoice.status != Invoice.STATUS_PAID and invoice.delivery_status != 'available':
                invoice.mark_available()
                db.session.commit()
            return True, []
        except Exception as exc:
            self.logger.error('Failed to prepare invoice %s for portal delivery: %s', invoice_id, exc)
            db.session.rollback()
            return False, ['Could not prepare invoice for portal delivery']

    def mark_invoice_paid(self, invoice_id: int) -> Tuple[bool, List[str], Optional[Invoice]]:
        try:
            invoice = Invoice.find_by_id(invoice_id)
            if not invoice:
                return False, ['Invoice does not exist'], None
            invoice.mark_paid()
            if invoice.job and not invoice.job.paid:
                invoice.job.paid = True
            db.session.commit()
            return True, [], invoice
        except Exception as exc:
            self.logger.error('Failed to mark invoice paid: %s', exc)
            db.session.rollback()
            return False, ['System error, please try again'], None
