"""
Billing Service
Business logic for billing and payment operations using SQLAlchemy ORM
"""
from typing import List, Optional, Dict, Any, Tuple
import logging
from flask import g
from app.extensions import db
from app.models.job import Job
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.services.invoice_service import InvoiceService


class BillingService:
    """Billing service class"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _current_tenant_id() -> Optional[int]:
        """Get current tenant ID from Flask g context"""
        return getattr(g, 'current_tenant_id', None)

    def get_unpaid_bills(self, customer_name: Optional[str] = None) -> List[Job]:
        """
        Get unpaid bills

        Args:
            customer_name: Customer name filter (optional)

        Returns:
            List of unpaid jobs
        """
        try:
            return Job.get_unpaid_jobs(customer_name)
        except Exception as e:
            self.logger.error(f"Failed to get unpaid bills: {e}")
            db.session.rollback()
            return []

    def get_overdue_bills(self, days_threshold: int = 14) -> List[Job]:
        """
        Get overdue bills

        Args:
            days_threshold: Overdue days threshold

        Returns:
            List of overdue jobs
        """
        try:
            return Job.get_overdue_jobs(days_threshold)
        except Exception as e:
            self.logger.error(f"Failed to get overdue bills: {e}")
            db.session.rollback()
            return []

    def get_all_bills_with_status(self) -> List[Job]:
        """Get all bills with status information as ORM objects."""
        try:
            return Job.get_all_with_customer_info()

        except Exception as e:
            self.logger.error(f"Failed to get all bills: {e}")
            db.session.rollback()
            return []

    def mark_customer_bills_as_paid(self, customer_id: int) -> Tuple[bool, List[str], int]:
        """
        Mark all unpaid bills for a customer as paid

        Args:
            customer_id: Customer ID

        Returns:
            (success, error_messages, count_marked)
        """
        try:
            customer = Customer.find_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], 0

            unpaid_jobs = customer.get_unpaid_jobs()
            if not unpaid_jobs:
                return False, ["This customer has no unpaid bills"], 0

            count = 0
            invoice_service = InvoiceService()
            for job in unpaid_jobs:
                job.mark_as_paid()
                invoice = invoice_service.get_invoice_for_job(job.job_id)
                if invoice and invoice.status != Invoice.STATUS_PAID:
                    invoice_service.mark_invoice_paid(invoice.invoice_id)
                count += 1

            db.session.commit()
            self.logger.info(f"Marked {count} bills as paid for customer {customer.full_name}")
            return True, [], count

        except Exception as e:
            self.logger.error(f"Failed to mark customer bills as paid: {e}")
            db.session.rollback()
            return False, ["System error, please try again"], 0

    def mark_job_as_paid(self, job_id: int) -> Tuple[bool, List[str]]:
        """
        Mark a single job as paid

        Args:
            job_id: Job ID

        Returns:
            (success, error_messages)
        """
        try:
            job = Job.find_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.paid:
                return False, ["Bill is already paid"]

            job.mark_as_paid()
            invoice_service = InvoiceService()
            invoice = invoice_service.get_invoice_for_job(job_id)
            if invoice and invoice.status != Invoice.STATUS_PAID:
                invoice_service.mark_invoice_paid(invoice.invoice_id)
            db.session.commit()
            self.logger.info(f"Job {job_id} marked as paid")
            return True, []

        except Exception as e:
            self.logger.error(f"Failed to mark bill as paid: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def get_customer_billing_summary(self, customer_id: int) -> Dict[str, Any]:
        """
        Get customer billing summary

        Args:
            customer_id: Customer ID

        Returns:
            Customer billing summary
        """
        try:
            summary = InvoiceService().get_customer_invoice_summary(customer_id)
            if not summary:
                return {}
            summary.setdefault('total_unpaid_amount', summary.get('unpaid_amount', 0))
            summary.setdefault('total_amount', summary.get('total_amount', 0))
            summary.setdefault('overdue_jobs', summary.get('overdue_jobs', 0))
            summary.setdefault('unpaid_jobs', summary.get('unpaid_jobs', 0))
            return summary

        except Exception as e:
            self.logger.error(f"Failed to get customer billing summary: {e}")
            db.session.rollback()
            return {}

    def get_billing_statistics(self) -> Dict[str, Any]:
        """Get overall billing statistics"""
        try:
            return InvoiceService().get_invoice_billing_statistics(self._current_tenant_id())

        except Exception as e:
            self.logger.error(f"Failed to get billing statistics: {e}")
            db.session.rollback()
            return self._get_empty_billing_stats()

    def get_customers_with_unpaid_bills(self) -> List[Dict[str, Any]]:
        """Get customers with unpaid bills"""
        try:
            invoices = InvoiceService().get_unpaid_invoices()
            grouped: Dict[int, Dict[str, Any]] = {}
            for invoice in invoices:
                customer_id = int(invoice.customer_id)
                entry = grouped.setdefault(
                    customer_id,
                    {
                        'customer_id': customer_id,
                        'first_name': getattr(invoice.customer, 'first_name', '') if invoice.customer else '',
                        'family_name': getattr(invoice.customer, 'family_name', '') if invoice.customer else invoice.customer_name_display,
                        'email': getattr(invoice.customer, 'email', '') if invoice.customer else invoice.customer_email_display,
                        'phone': getattr(invoice.customer, 'phone', '') if invoice.customer else '',
                        'unpaid_count': 0,
                        'unpaid_amount': 0.0,
                    },
                )
                entry['unpaid_count'] += 1
                entry['unpaid_amount'] += float(invoice.total_amount or 0)
            results = list(grouped.values())
            results.sort(key=lambda row: (-row['unpaid_amount'], row['family_name'], row['first_name']))
            return results

        except Exception as e:
            self.logger.error(f"Failed to get customers with unpaid bills: {e}")
            db.session.rollback()
            return []

    def _get_empty_billing_stats(self) -> Dict[str, Any]:
        """Return empty billing statistics"""
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
            'total_customers': 0,
            'new_customers_month': 0,
            'paid_bills': 0,
            'unpaid_bills': 0,
            'overdue_bills': 0,
            'payment_rate': 0.0,
        }
