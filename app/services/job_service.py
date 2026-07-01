"""
Job Service
Business logic for work order operations using SQLAlchemy ORM
"""
from typing import List, Optional, Dict, Any, Tuple
from datetime import date, timedelta
import logging
from flask import g
from app.extensions import db
from app.models.job import Job, JobStatusHistory
from app.models.invoice import Invoice
from app.models.customer import Customer
from app.models.service import Service
from app.models.part import Part
from app.models.vehicle import Vehicle
from app.models.user import User
from app.models.tenant_membership import TenantMembership
from app.models.tenant import Tenant


class JobService:
    """Job service class"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _current_tenant_id():
        """Get current tenant ID from Flask g context"""
        return getattr(g, 'current_tenant_id', None)

    def get_current_jobs(self, page: int = 1, per_page: int = 10) -> Tuple[List[Job], int, int]:
        """
        Get current incomplete jobs with pagination

        Args:
            page: Page number
            per_page: Records per page

        Returns:
            (jobs_list, total_count, total_pages)
        """
        try:
            jobs, total = Job.get_current_jobs(page, per_page)
            total_pages = (total + per_page - 1) // per_page

            return jobs, total, total_pages

        except Exception as e:
            self.logger.error(f"Failed to get current jobs: {e}")
            raise

    def get_job_by_id(self, job_id: int) -> Optional[Job]:
        """Get job by ID"""
        try:
            return Job.find_by_id(job_id)
        except Exception as e:
            self.logger.error(f"Failed to get job (ID: {job_id}): {e}")
            raise

    def get_job_details(self, job_id: int) -> Dict[str, Any]:
        """
        Get detailed job information

        Args:
            job_id: Job ID

        Returns:
            Job details dictionary
        """
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return {}

            # Get all available services and parts
            all_services = Service.get_all_sorted()
            all_parts = Part.get_all_sorted()
            services = job.get_services()
            parts = job.get_parts()
            services_total = sum(item.get('total_cost', 0) for item in services)
            parts_total = sum(item.get('total_cost', 0) for item in parts)
            job_total = float(job.total_cost or (services_total + parts_total))

            return {
                'job_info': job.to_dict(),
                'services': services,
                'parts': parts,
                'services_total': float(services_total),
                'parts_total': float(parts_total),
                'estimated_total': float(job_total),
                'attachments': [a.to_dict() for a in getattr(job, 'attachments', [])],
                'status_history': [h.to_dict() for h in getattr(job, 'status_history', [])],
                'invoice': job.invoice.to_dict() if getattr(job, 'invoice', None) else None,
                'available_technicians': [u.to_dict() for u in self.get_available_technicians(job.tenant_id)],
                'all_services': [s.to_dict() for s in all_services],
                'all_parts': [p.to_dict() for p in all_parts],
                'job_completed': job.completed
            }

        except Exception as e:
            self.logger.error(f"Failed to get job details (ID: {job_id}): {e}")
            return {}

    def add_service_to_job(self, job_id: int, service_id: int, quantity: int) -> Tuple[bool, List[str]]:
        """
        Add service to job

        Args:
            job_id: Job ID
            service_id: Service ID
            quantity: Quantity

        Returns:
            (success, error_messages)
        """
        try:
            if quantity <= 0:
                return False, ["Quantity must be greater than 0"]

            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.completed:
                return False, ["Cannot modify a completed job"]

            service = Service.find_by_id(service_id)
            if not service:
                return False, ["Service does not exist"]

            job.add_service(service_id, quantity)
            self.logger.info(f"Added service {service.service_name} to job {job_id}")
            return True, []

        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to add service: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def add_part_to_job(self, job_id: int, part_id: int, quantity: int) -> Tuple[bool, List[str]]:
        """
        Add part to job

        Args:
            job_id: Job ID
            part_id: Part ID
            quantity: Quantity

        Returns:
            (success, error_messages)
        """
        try:
            if quantity <= 0:
                return False, ["Quantity must be greater than 0"]

            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.completed:
                return False, ["Cannot modify a completed job"]

            part = Part.find_by_id(part_id)
            if not part:
                return False, ["Part does not exist"]

            job.add_part(part_id, quantity)
            self.logger.info(f"Added part {part.part_name} to job {job_id}")
            return True, []

        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to add part: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def update_service_quantity(self, job_id: int, service_id: int, quantity: int) -> Tuple[bool, List[str]]:
        """Update or remove a job service line item."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]
            if job.completed:
                return False, ["Cannot modify a completed job"]
            if quantity is None:
                return False, ["Quantity is required"]
            job.update_service_quantity(service_id, quantity)
            return True, []
        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to update service quantity: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def remove_service_from_job(self, job_id: int, service_id: int) -> Tuple[bool, List[str]]:
        """Remove a service line item from a job."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]
            if job.completed:
                return False, ["Cannot modify a completed job"]
            job.remove_service(service_id)
            return True, []
        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to remove service: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def update_part_quantity(self, job_id: int, part_id: int, quantity: int) -> Tuple[bool, List[str]]:
        """Update or remove a job part line item."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]
            if job.completed:
                return False, ["Cannot modify a completed job"]
            if quantity is None:
                return False, ["Quantity is required"]
            job.update_part_quantity(part_id, quantity)
            return True, []
        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to update part quantity: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def remove_part_from_job(self, job_id: int, part_id: int) -> Tuple[bool, List[str]]:
        """Remove a part line item from a job."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]
            if job.completed:
                return False, ["Cannot modify a completed job"]
            job.remove_part(part_id)
            return True, []
        except ValueError as e:
            return False, [str(e)]
        except Exception as e:
            self.logger.error(f"Failed to remove part: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]


    def mark_job_as_completed(self, job_id: int) -> Tuple[bool, List[str]]:
        """
        Mark job as completed

        Args:
            job_id: Job ID

        Returns:
            (success, error_messages)
        """
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.completed:
                return False, ["Job is already completed"]

            job.mark_as_completed()

            from app.services.invoice_service import InvoiceService
            invoice_service = InvoiceService()
            existing_invoice = invoice_service.get_invoice_for_job(job_id)
            if existing_invoice:
                if existing_invoice.status != Invoice.STATUS_PAID and existing_invoice.delivery_status != 'available':
                    existing_invoice.mark_available()
            else:
                snapshot = invoice_service.build_invoice_snapshot(job, due_days=14)
                invoice = Invoice(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    status=Invoice.STATUS_DRAFT,
                    **snapshot,
                )
                db.session.add(invoice)
                invoice.mark_available()

            db.session.commit()
            self.logger.info(f"Job {job_id} marked as completed")
            return True, []

        except Exception as e:
            self.logger.error(f"Failed to mark job as completed: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def mark_job_as_paid(self, job_id: int) -> Tuple[bool, List[str]]:
        """
        Mark job as paid

        Args:
            job_id: Job ID

        Returns:
            (success, error_messages)
        """
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.paid:
                return False, ["Job is already paid"]

            job.mark_as_paid()
            db.session.commit()
            self.logger.info(f"Job {job_id} marked as paid")
            return True, []

        except Exception as e:
            self.logger.error(f"Failed to mark job as paid: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def get_all_jobs_with_customer_info(self) -> List[Job]:
        """Get all jobs with customer information"""
        try:
            return Job.get_all_with_customer_info()
        except Exception as e:
            self.logger.error(f"Failed to get all jobs: {e}")
            return []

    def get_job_statistics(self) -> Dict[str, Any]:
        """Get job statistics (tenant-scoped via model)"""
        try:
            jobs = Job.get_all_with_customer_info()
            total_jobs = len(jobs)
            completed_jobs = len([job for job in jobs if job.status in (Job.STATUS_COMPLETED, Job.STATUS_DELIVERED) or job.completed])
            in_progress_jobs = len([job for job in jobs if job.status == Job.STATUS_IN_PROGRESS])
            awaiting_parts_jobs = len([job for job in jobs if job.status == Job.STATUS_AWAITING_PARTS])
            pending_jobs = len([job for job in jobs if job.status == Job.STATUS_DRAFT])
            unpaid_jobs = len([job for job in jobs if job.completed and not job.paid])
            overdue_jobs = len([job for job in jobs if not job.completed and getattr(job, 'job_date', None) and job.job_date < date.today()])

            return {
                'total_jobs': total_jobs,
                'completed_jobs': completed_jobs,
                'in_progress_jobs': in_progress_jobs + awaiting_parts_jobs,
                'awaiting_parts_jobs': awaiting_parts_jobs,
                'pending_jobs': pending_jobs,
                'open_jobs': in_progress_jobs + awaiting_parts_jobs + pending_jobs,
                'unpaid_jobs': unpaid_jobs,
                'overdue_jobs': overdue_jobs,
                'completion_rate': (completed_jobs / total_jobs * 100) if total_jobs > 0 else 0,
                'payment_rate': ((total_jobs - unpaid_jobs) / total_jobs * 100) if total_jobs > 0 else 0,
            }

        except Exception as e:
            self.logger.error(f"Failed to get job statistics: {e}")
            return {
                'total_jobs': 0,
                'completed_jobs': 0,
                'in_progress_jobs': 0,
                'awaiting_parts_jobs': 0,
                'pending_jobs': 0,
                'open_jobs': 0,
                'unpaid_jobs': 0,
                'overdue_jobs': 0,
                'completion_rate': 0,
                'payment_rate': 0,
            }

    def get_available_technicians(self, tenant_id: Optional[int] = None) -> List[User]:
        """Return active technicians for a workshop."""
        try:
            query = db.select(User).where(User.is_active == True)
            if tenant_id:
                query = query.join(TenantMembership, TenantMembership.user_id == User.user_id).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.status == 'active',
                    TenantMembership.role == 'technician',
                )
            else:
                query = query.join(TenantMembership, TenantMembership.user_id == User.user_id).where(
                    TenantMembership.status == 'active',
                    TenantMembership.role == 'technician',
                )
            query = query.order_by(User.username)
            return list(db.session.execute(query).scalars())
        except Exception as e:
            self.logger.error(f"Failed to get available technicians: {e}")
            return []

    def detect_booking_conflicts(self, tenant_id: int, job_date: date, vehicle_id: Optional[int] = None, technician_id: Optional[int] = None, exclude_job_id: Optional[int] = None) -> List[str]:
        """Detect obvious same-day booking conflicts."""
        conflicts: List[str] = []
        try:
            filters = [Job.tenant_id == tenant_id, Job.job_date == job_date]
            if exclude_job_id:
                filters.append(Job.job_id != exclude_job_id)

            same_day_jobs = list(db.session.execute(db.select(Job).where(*filters)).scalars())
            if vehicle_id and any(job.vehicle_id == vehicle_id for job in same_day_jobs):
                conflicts.append('This vehicle already has a booking for the selected date.')
            if technician_id and any(job.assigned_to == technician_id for job in same_day_jobs):
                conflicts.append('The selected technician is already assigned to another job on this date.')

            tenant = Tenant.find_by_id(tenant_id)
            max_jobs = None
            if tenant and isinstance(tenant.settings, dict):
                max_jobs = tenant.settings.get('daily_job_capacity') or tenant.settings.get('max_jobs_per_day')
            if max_jobs is not None:
                try:
                    max_jobs_int = int(max_jobs)
                    if len(same_day_jobs) >= max_jobs_int:
                        conflicts.append('This workshop has reached its daily capacity for the selected date.')
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(f"Failed to detect booking conflicts: {e}")
        return conflicts

    def get_workshop_calendar(self, tenant_id: Optional[int] = None, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, Any]:
        """Return calendar-ready workshop booking data."""
        try:
            query = db.select(Job)
            if tenant_id:
                query = query.where(Job.tenant_id == tenant_id)
            if start_date:
                query = query.where(Job.job_date >= start_date)
            if end_date:
                query = query.where(Job.job_date <= end_date)
            query = query.order_by(Job.job_date.asc(), Job.job_id.asc())
            jobs = list(db.session.execute(query).scalars())
            grouped: Dict[str, List[dict]] = {}
            for job in jobs:
                grouped.setdefault(job.job_date.isoformat(), []).append(job.to_dict())
            return {
                'jobs': [job.to_dict() for job in jobs],
                'grouped_jobs': grouped,
                'job_count': len(jobs),
                'technicians': [u.to_dict() for u in self.get_available_technicians(tenant_id)],
            }
        except Exception as e:
            self.logger.error(f"Failed to build workshop calendar: {e}")
            return {'jobs': [], 'grouped_jobs': {}, 'job_count': 0, 'technicians': []}

    def update_job_status(self, job_id: int, new_status: str, changed_by_user_id: Optional[int] = None, note: Optional[str] = None) -> Tuple[bool, List[str], Optional[Job]]:
        """Update a job status and persist an audit row."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ['Job does not exist'], None
            job.set_status(new_status, changed_by_user_id=changed_by_user_id, note=note)
            if new_status in (Job.STATUS_COMPLETED, Job.STATUS_DELIVERED):
                job.sync_vehicle_mileage()
            db.session.commit()
            if new_status in (Job.STATUS_COMPLETED, Job.STATUS_DELIVERED):
                try:
                    from app.services.invoice_service import InvoiceService
                    InvoiceService().create_invoice_for_job(job_id, send_email=False)
                except Exception as invoice_exc:
                    self.logger.error(f'Invoice creation failed for job {job_id}: {invoice_exc}')
            return True, [], job
        except Exception as e:
            self.logger.error(f"Failed to update job status: {e}")
            db.session.rollback()
            return False, ['System error, please try again'], None

    def assign_technician_to_job(self, job_id: int, technician_user_id: Optional[int], changed_by_user_id: Optional[int] = None) -> Tuple[bool, List[str], Optional[Job]]:
        """Assign a technician to a job."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ['Job does not exist'], None
            if technician_user_id:
                tech = db.session.get(User, technician_user_id)
                if not tech:
                    return False, ['Technician does not exist'], None
            job.assign_technician(technician_user_id, changed_by_user_id=changed_by_user_id, note='Technician assignment updated')
            db.session.commit()
            return True, [], job
        except Exception as e:
            self.logger.error(f"Failed to assign technician: {e}")
            db.session.rollback()
            return False, ['System error, please try again'], None

    def add_internal_note_to_job(self, job_id: int, note: str, changed_by_user_id: Optional[int] = None) -> Tuple[bool, List[str], Optional[Job]]:
        """Append an internal note to a job."""
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ['Job does not exist'], None
            job.add_internal_note(note, changed_by_user_id=changed_by_user_id)
            db.session.commit()
            return True, [], job
        except Exception as e:
            self.logger.error(f"Failed to add internal note: {e}")
            db.session.rollback()
            return False, ['System error, please try again'], None

    def create_job(self, customer_id: int, job_date: date, vehicle_id: Optional[int] = None, mileage: Optional[int] = None, status: str = Job.STATUS_IN_PROGRESS, assigned_to: Optional[int] = None, internal_notes: Optional[str] = None, changed_by_user_id: Optional[int] = None) -> Tuple[bool, List[str], Optional[Job]]:
        """
        Create a new job

        Args:
            customer_id: Customer ID
            job_date: Job date

        Returns:
            (success, error_messages, job)
        """
        try:
            customer = Customer.find_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], None

            vehicle = None
            if vehicle_id:
                vehicle = Vehicle.find_by_id(vehicle_id)
                if not vehicle:
                    return False, ["Vehicle does not exist"], None
                if vehicle.customer_id != customer_id:
                    return False, ["Selected vehicle does not belong to the customer"], None

            if job_date < date.today():
                return False, ["Job date cannot be earlier than today"], None

            if mileage is not None and mileage < 0:
                return False, ["Mileage cannot be negative"], None

            job_mileage = mileage if mileage is not None else (vehicle.mileage if vehicle else None)

            job = Job(
                job_date=job_date,
                customer=customer_id,
                vehicle_id=vehicle_id if vehicle else None,
                mileage=job_mileage,
                tenant_id=self._current_tenant_id(),
                total_cost=0.0,
                completed=False,
                paid=False,
                assigned_to=assigned_to,
                internal_notes=internal_notes,
            )
            job.save()
            job.set_status(status, changed_by_user_id=changed_by_user_id, note='Work order created', initial=True)
            job.sync_vehicle_mileage()
            db.session.commit()

            self.logger.info(f"Created job for customer {customer_id}")
            return True, [], job

        except Exception as e:
            self.logger.error(f"Failed to create job: {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None

    def delete_job(self, job_id: int) -> Tuple[bool, List[str]]:
        """
        Delete job

        Args:
            job_id: Job ID

        Returns:
            (success, error_messages)
        """
        try:
            job = self.get_job_by_id(job_id)
            if not job:
                return False, ["Job does not exist"]

            if job.completed:
                return False, ["Cannot delete a completed job"]

            if job.job_services or job.job_parts:
                return False, ["Cannot delete job with services or parts"]

            job.delete()
            self.logger.info(f"Deleted job {job_id}")
            return True, []

        except Exception as e:
            self.logger.error(f"Failed to delete job: {e}")
            db.session.rollback()
            return False, ["System error, please try again"]
