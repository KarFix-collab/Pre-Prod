"""
Customer Service
Business logic for customer operations using SQLAlchemy ORM
"""
from typing import List, Optional, Dict, Any, Tuple
from datetime import date, timedelta, datetime
import logging
import re
import secrets
import requests
from flask import current_app
from sqlalchemy import and_, update, or_
from app.extensions import db
from app.models.customer import Customer
from app.models.job import Job
from app.models.vehicle import Vehicle
from app.services.job_service import JobService
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.security import PasswordSecurity


class CustomerService:
    """Customer service class"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _get_portal_status_store(self, tenant: Optional[Tenant]) -> Dict[str, Any]:
        """Return the tenant-level portal status registry."""
        if not tenant:
            return {}
        settings = tenant.settings or {}
        store = settings.get('customer_portal_statuses') or {}
        return store if isinstance(store, dict) else {}

    def _save_portal_status(self, tenant: Optional[Tenant], customer_id: int, status_data: Dict[str, Any]) -> None:
        """Persist the latest portal provisioning outcome in tenant settings."""
        if not tenant:
            return
        settings = dict(tenant.settings or {})
        store = settings.get('customer_portal_statuses')
        if not isinstance(store, dict):
            store = {}
        store[str(customer_id)] = status_data
        settings['customer_portal_statuses'] = store
        tenant.settings = settings

    def _default_reset_password_redirect(self) -> str:
        """Return the canonical customer reset-password redirect URL."""
        try:
            configured = current_app.config.get('PORTAL_RESET_PASSWORD_URL')
        except Exception:
            configured = None

        redirect_url = str(configured or '').strip()
        if not redirect_url:
            try:
                from flask import url_for
                redirect_url = url_for('auth.reset_password', _external=True)
            except Exception:
                redirect_url = 'https://localhost:5000/auth/reset-password'

        redirect_url = str(redirect_url).strip()
        if redirect_url.startswith('http://'):
            redirect_url = 'https://' + redirect_url[7:]
        return redirect_url or 'https://localhost:5000/auth/reset-password'

    def _normalize_reset_password_redirect(self, redirect_to: Optional[str]) -> str:
        """Force any recovery redirect to land on the reset-password page."""
        default_redirect = self._default_reset_password_redirect()
        if not redirect_to:
            return default_redirect

        redirect_to = str(redirect_to).strip()
        if redirect_to.startswith('http://'):
            redirect_to = 'https://' + redirect_to[7:]

        try:
            from urllib.parse import urlparse
            parsed = urlparse(redirect_to)
            default_parsed = urlparse(default_redirect)
            if parsed.scheme not in ('https', ''):
                return default_redirect
            if parsed.netloc and parsed.netloc != default_parsed.netloc:
                return default_redirect
            if parsed.path.rstrip('/') != '/auth/reset-password':
                return default_redirect
            return redirect_to
        except Exception:
            return default_redirect

    def _generate_unique_username(self, email: str) -> str:
        """Generate a unique username based on an email address."""
        base_username = (email.split('@')[0] if email and '@' in email else 'customer').strip().lower()
        base_username = re.sub(r'[^a-z0-9._-]+', '', base_username) or 'customer'
        username = base_username
        counter = 1
        while User.find_by_username(username):
            username = f"{base_username}{counter}"
            counter += 1
        return username

    def get_portal_access_status(self, customer_id: int) -> Dict[str, Any]:
        """Return the latest portal access status for a customer."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return {}

            linked_user = db.session.execute(
                db.select(User).where(User.customer_id == customer.customer_id).order_by(User.user_id)
            ).scalar_one_or_none()

            tenant = (
                customer.preferred_tenant
                or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None)
                or customer.tenant
                or (Tenant.find_by_id(customer.tenant_id) if customer.tenant_id else None)
            )
            status_store = self._get_portal_status_store(tenant)
            stored_status = status_store.get(str(customer.customer_id)) if status_store else None
            if not isinstance(stored_status, dict):
                stored_status = {}

            provisioning_status = stored_status.get('provisioning_status')
            if not provisioning_status:
                provisioning_status = 'enabled' if linked_user else 'not_enabled'

            enabled = provisioning_status in {'enabled', 'ready'}
            local_account_linked = bool(linked_user)

            return {
                'enabled': enabled,
                'customer_id': customer.customer_id,
                'customer_email': customer.email,
                'tenant_id': customer.tenant_id,
                'linked_user': linked_user,
                'linked_username': (stored_status.get('linked_username') if stored_status else None) or (linked_user.username if linked_user else None),
                'linked_email': (stored_status.get('linked_email') if stored_status else None) or (linked_user.email if linked_user else customer.email),
                'provisioning_status': provisioning_status,
                'portal_ready': bool(stored_status.get('portal_ready', enabled)),
                'local_account_linked': bool(stored_status.get('local_account_linked', local_account_linked)),
                'remote_errors': stored_status.get('remote_errors') or [],
                'provisioning_notes': stored_status.get('provisioning_notes') or [],
                'last_provisioned_at': stored_status.get('updated_at'),
                'provisioning_state_source': 'persisted' if stored_status else 'linked_user',
            }
        except Exception as e:
            self.logger.error(f"Failed to get portal access status (ID: {customer_id}): {e}")
            return {}

    def _send_portal_recovery_email(
        self,
        supabase_url: str,
        anon_key: str,
        email: str,
        redirect_to: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Send a password-recovery email with a slightly longer timeout and one retry.

        Supabase recovery requests can occasionally take longer than the default
        timeout because the provider also performs email delivery work. We allow a
        second attempt for transient network timeouts so portal provisioning does
        not incorrectly report failure when the account was created successfully.
        """
        recover_payload = {'email': email, 'redirect_to': self._normalize_reset_password_redirect(redirect_to)}

        headers = {
            'apikey': anon_key,
            'Content-Type': 'application/json',
        }

        last_error: Optional[str] = None
        notes: List[str] = []
        timeout = (5, 45)

        for attempt in range(2):
            try:
                recover_response = requests.post(
                    f"{supabase_url}/auth/v1/recover",
                    json=recover_payload,
                    headers=headers,
                    timeout=timeout,
                )
                if recover_response.ok:
                    return True, None, notes

                try:
                    payload = recover_response.json()
                except Exception:
                    payload = {}
                message = payload.get('msg') or payload.get('message') or recover_response.text

                if recover_response.status_code == 429 or 'rate limit' in (message or '').lower():
                    notes.append(
                        'Supabase account created but the password-reset email could not be sent '
                        'yet (email rate limit reached). Click "Enable Portal Access" again in a '
                        'few minutes to resend, or use the Supabase dashboard to send manually.'
                    )
                    return False, None, notes

                last_error = message or 'Could not send recovery email'
                break
            except requests.Timeout:
                last_error = f'Recovery email timed out after {timeout[1]} seconds'
                if attempt == 0:
                    notes.append('Recovery email request timed out once and will be retried automatically.')
                    continue
                break
            except Exception as exc:
                last_error = f'Recovery email error: {exc}'
                if attempt == 0:
                    notes.append('Recovery email request hit a transient error and will be retried automatically.')
                    continue
                break

        return False, last_error, notes

    def _get_supabase_admin_headers(self, service_role_key: str) -> Dict[str, str]:
        """Headers for Supabase admin API requests."""
        return {
            'apikey': service_role_key,
            'Authorization': f'Bearer {service_role_key}',
            'Content-Type': 'application/json',
        }

    def _find_supabase_user_id_by_email(
        self,
        supabase_url: str,
        service_role_key: str,
        email: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Look up a Supabase auth user ID by email using the admin list endpoint."""
        if not supabase_url or not service_role_key or not email:
            return None, 'Supabase admin lookup is not configured'

        headers = self._get_supabase_admin_headers(service_role_key)
        target_email = email.strip().lower()

        for page in range(1, 6):
            try:
                response = requests.get(
                    f"{supabase_url}/auth/v1/admin/users",
                    headers=headers,
                    params={'page': page, 'per_page': 100},
                    timeout=15,
                )
            except Exception as exc:
                return None, f'Supabase admin user lookup error: {exc}'

            if not response.ok:
                try:
                    payload = response.json()
                except Exception:
                    payload = {}
                message = payload.get('msg') or payload.get('message') or response.text
                return None, message or 'Could not look up Supabase user'

            try:
                payload = response.json()
            except Exception:
                payload = {}

            users = payload.get('users')
            if users is None and isinstance(payload.get('data'), list):
                users = payload.get('data')
            if users is None and isinstance(payload, list):
                users = payload
            if not isinstance(users, list):
                users = []

            for item in users:
                if not isinstance(item, dict):
                    continue
                item_email = (item.get('email') or '').strip().lower()
                if item_email == target_email:
                    user_id = item.get('id') or item.get('user_id')
                    return (str(user_id) if user_id else None), None

            if len(users) < 100:
                break

        return None, None

    def _sync_supabase_portal_password(
        self,
        supabase_url: str,
        service_role_key: str,
        supabase_user_id: Optional[str],
        password: str,
    ) -> Tuple[bool, Optional[str]]:
        """Update the Supabase auth password for a portal user."""
        if not supabase_url or not service_role_key or not supabase_user_id or not password:
            return False, 'Supabase admin password sync is not configured'

        headers = self._get_supabase_admin_headers(service_role_key)
        try:
            response = requests.patch(
                f"{supabase_url}/auth/v1/admin/users/{supabase_user_id}",
                json={'password': password},
                headers=headers,
                timeout=15,
            )
        except Exception as exc:
            return False, f'Supabase password sync error: {exc}'

        if response.ok:
            return True, None

        try:
            payload = response.json()
        except Exception:
            payload = {}
        message = payload.get('msg') or payload.get('message') or response.text
        return False, message or 'Could not update Supabase password'

    def enable_portal_access(self, customer_id: int, redirect_to: Optional[str] = None) -> Tuple[bool, List[str], Optional[User], Dict[str, Any]]:
        """Create or link a customer portal account and invite the customer."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], None, {}

            if not customer.email or not customer.email.strip():
                return False, ["Customer email is required to enable portal access"], None, {}

            email = customer.email.strip().lower()
            supabase_url = (current_app.config.get('SUPABASE_URL') or '').rstrip('/')
            anon_key = current_app.config.get('SUPABASE_ANON_KEY', '')
            service_role_key = current_app.config.get('SUPABASE_SERVICE_ROLE_KEY', '')
            supabase_configured = bool(supabase_url and anon_key)
            supabase_admin_configured = bool(supabase_configured and service_role_key)
            temp_password = secrets.token_urlsafe(12)

            linked_user = db.session.execute(
                db.select(User).where(User.customer_id == customer.customer_id).order_by(User.user_id)
            ).scalar_one_or_none()

            created_local_user = False
            if linked_user:
                user = linked_user
            else:
                user = User(
                    username=self._generate_unique_username(email),
                    email=email,
                    is_active=True,
                    email_verified=False,
                )
                db.session.add(user)
                db.session.flush()
                created_local_user = True

            user.email = email
            user.customer_id = customer.customer_id
            preferred_tenant_id = customer.preferred_tenant_id or customer.tenant_id
            if preferred_tenant_id:
                user.preferred_tenant_id = preferred_tenant_id
            user.is_active = True
            if not user.username:
                user.username = self._generate_unique_username(email)

            # Keep a local bootstrap password so the portal can still be opened
            # even if the Supabase password sync or recovery email is delayed or fails.
            local_hash, local_salt = PasswordSecurity.hash_password(temp_password)
            user.password_hash = f"{local_hash}:{local_salt}"
            user.email_verified = True

            supabase_sign_up_ok = False
            supabase_recover_ok = False
            supabase_password_sync_ok = False
            remote_errors: List[str] = []
            provisioning_notes: List[str] = []

            if not supabase_configured:
                provisioning_notes.append('Supabase auth is not configured on this deployment')
            else:
                headers = {
                    'apikey': anon_key,
                    'Content-Type': 'application/json',
                }

                supabase_user_id: Optional[str] = None

                try:
                    signup_response = requests.post(
                        f"{supabase_url}/auth/v1/signup",
                        json={
                            'email': email,
                            'password': temp_password,
                            'data': {
                                'full_name': customer.full_name,
                                'customer_id': str(customer.customer_id),
                            },
                        },
                        headers=headers,
                        timeout=15,
                    )
                    if signup_response.ok:
                        supabase_sign_up_ok = True
                        try:
                            payload = signup_response.json()
                        except Exception:
                            payload = {}
                        supabase_user = payload.get('user') or payload.get('data', {}).get('user') or {}
                        if isinstance(supabase_user, dict):
                            supabase_user_id = supabase_user.get('id') or supabase_user.get('user_id')
                    else:
                        try:
                            payload = signup_response.json()
                        except Exception:
                            payload = {}
                        message = payload.get('msg') or payload.get('message') or signup_response.text
                        # Supabase returns 422 "User already registered" when re-provisioning
                        # an existing account. Treat it as a soft success and continue to sync
                        # the password directly via the admin API.
                        already_registered = (
                            signup_response.status_code == 422
                            or 'already registered' in (message or '').lower()
                            or 'already been registered' in (message or '').lower()
                        )
                        if already_registered:
                            supabase_sign_up_ok = True
                            provisioning_notes.append('Supabase account already exists — syncing the temporary password.')
                        elif message:
                            remote_errors.append(message)
                except Exception as e:
                    remote_errors.append(f"Supabase signup error: {e}")

                if supabase_admin_configured:
                    if not supabase_user_id:
                        supabase_user_id, lookup_error = self._find_supabase_user_id_by_email(
                            supabase_url=supabase_url,
                            service_role_key=service_role_key,
                            email=email,
                        )
                        if lookup_error:
                            remote_errors.append(lookup_error)

                    if supabase_user_id:
                        supabase_password_sync_ok, password_sync_error = self._sync_supabase_portal_password(
                            supabase_url=supabase_url,
                            service_role_key=service_role_key,
                            supabase_user_id=supabase_user_id,
                            password=temp_password,
                        )
                        if supabase_password_sync_ok:
                            provisioning_notes.append('Supabase portal password updated to match the temporary password.')
                        elif password_sync_error:
                            remote_errors.append(password_sync_error)
                    else:
                        provisioning_notes.append('Supabase service role key could not locate the portal account for direct password sync.')
                elif supabase_configured:
                    provisioning_notes.append('Supabase service role key is not configured; relying on the local portal password fallback.')

                recover_ok, recover_error, recover_notes = self._send_portal_recovery_email(
                    supabase_url=supabase_url,
                    anon_key=anon_key,
                    email=email,
                    redirect_to=self._normalize_reset_password_redirect(redirect_to),
                )
                supabase_recover_ok = recover_ok
                if recover_notes:
                    provisioning_notes.extend(recover_notes)
                if recover_error:
                    remote_errors.append(recover_error)

            portal_ready = bool(supabase_sign_up_ok or supabase_recover_ok)
            # Determine provisioning status:
            # - 'ready'         : account exists in Supabase, recovery email sent
            # - 'ready_no_email': account exists, recovery email not sent (rate limited)
            # - 'partial'       : account exists, but an unexpected non-rate-limit error occurred
            # - 'failed'        : Supabase configured but signup failed entirely
            # - 'local_only'    : Supabase not configured
            rate_limited_only = (
                supabase_sign_up_ok
                and not supabase_recover_ok
                and not remote_errors
                and any('rate limit' in n.lower() for n in provisioning_notes)
            )
            if portal_ready and not remote_errors:
                provisioning_status = 'ready'
            elif rate_limited_only:
                provisioning_status = 'ready_no_email'
            elif portal_ready and remote_errors:
                provisioning_status = 'partial'
            elif supabase_configured:
                provisioning_status = 'failed'
            else:
                provisioning_status = 'local_only'

            tenant = (
                customer.preferred_tenant
                or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None)
                or customer.tenant
                or (Tenant.find_by_id(customer.tenant_id) if customer.tenant_id else None)
            )
            status_payload = {
                'provisioning_status': provisioning_status,
                'portal_ready': portal_ready,
                'local_account_linked': True,
                'linked_user_id': user.user_id,
                'linked_username': user.username,
                'linked_email': user.email,
                'remote_errors': remote_errors,
                'provisioning_notes': provisioning_notes,
                'supabase_configured': supabase_configured,
                'supabase_sign_up_ok': supabase_sign_up_ok,
                'supabase_recover_ok': supabase_recover_ok,
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            }
            self._save_portal_status(tenant, customer.customer_id, status_payload)
            db.session.commit()

            details = {
                'customer': customer,
                'user': user,
                'created_local_user': created_local_user,
                'supabase_configured': supabase_configured,
                'supabase_sign_up_ok': supabase_sign_up_ok,
                'supabase_recover_ok': supabase_recover_ok,
                'portal_ready': portal_ready,
                'provisioning_status': provisioning_status,
                'provisioning_notes': provisioning_notes,
                'temp_password': temp_password,
                'supabase_password_sync_ok': supabase_password_sync_ok,
                'remote_errors': remote_errors,
                'local_account_linked': True,
                'persisted_portal_status': status_payload,
            }
            return portal_ready, remote_errors or provisioning_notes, user, details

        except Exception as e:
            self.logger.error(f"Failed to enable portal access (ID: {customer_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None, {}

    def get_all_customers(self, sorted_by_name: bool = True) -> List[Customer]:
        """
        Get all customers

        Args:
            sorted_by_name: Whether to sort by name

        Returns:
            List of customers
        """
        try:
            if sorted_by_name:
                return Customer.get_all_sorted()
            else:
                return Customer.find_all()
        except Exception as e:
            self.logger.error(f"Failed to get customer list: {e}")
            raise

    def get_customer_by_id(self, customer_id: int) -> Optional[Customer]:
        """Get customer by ID"""
        try:
            return Customer.find_by_id(customer_id)
        except Exception as e:
            self.logger.error(f"Failed to get customer (ID: {customer_id}): {e}")
            raise

    def get_customer_by_email(self, email: str, tenant_id: Optional[int] = None) -> Optional[Customer]:
        """Get customer by email, optionally scoped to a tenant."""
        try:
            if not email or not email.strip():
                return None

            query = db.select(Customer).where(Customer.email.ilike(email.strip()))
            if tenant_id:
                query = query.where(Customer.tenant_id == tenant_id)
            query = query.order_by(Customer.customer_id)
            return db.session.execute(query).scalar_one_or_none()
        except Exception as e:
            self.logger.error(f"Failed to get customer by email ({email}): {e}")
            raise

    def get_customer_profiles_by_email(self, email: str) -> List[Customer]:
        """Get all tenant-specific customer profiles for a login email."""
        try:
            if not email or not email.strip():
                return []
            query = (
                db.select(Customer)
                .where(Customer.email.ilike(email.strip()))
                .order_by(Customer.tenant_id, Customer.customer_id)
            )
            return list(db.session.execute(query).scalars())
        except Exception as e:
            self.logger.error(f"Failed to get customer profiles for email ({email}): {e}")
            return []

    def get_workshop_options_for_email(self, email: str, selected_tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return all registered workshops for customer selection."""
        options: List[Dict[str, Any]] = []
        for tenant in Tenant.query.order_by(Tenant.name).all():
            options.append({
                'tenant_id': tenant.tenant_id,
                'tenant_name': tenant.name,
                'tenant_slug': tenant.slug,
                'customer_id': None,
                'is_current': tenant.tenant_id == selected_tenant_id,
            })
        return options

    def search_customers(self, search_term: str, search_type: str = 'both') -> List[Customer]:
        """
        Search customers

        Args:
            search_term: Search keyword
            search_type: Search type ('first_name', 'family_name', 'both')

        Returns:
            List of matching customers
        """
        try:
            if not search_term or not search_term.strip():
                return self.get_all_customers()

            return Customer.search_by_name(search_term.strip(), search_type)
        except Exception as e:
            self.logger.error(f"Failed to search customers: {e}")
            raise

    def create_customer(self, customer_data: Dict[str, Any]) -> Tuple[bool, List[str], Optional[Customer]]:
        """
        Create a new customer

        Args:
            customer_data: Customer data dictionary

        Returns:
            (success, error_messages, customer)
        """
        try:
            customer = Customer(**customer_data)

            # Validate data
            validation_errors = customer.validate()
            if validation_errors:
                return False, validation_errors, None

            # Save customer
            customer.save()

            # If a local Supabase-authenticated user already exists with the same
            # email address, link the portal account immediately.
            linked_user = User.find_by_email(customer.email)
            if linked_user and not linked_user.get_tenants():
                linked_user.customer_id = customer.customer_id
                preferred_tenant_id = customer.preferred_tenant_id or customer.tenant_id
                if preferred_tenant_id:
                    linked_user.preferred_tenant_id = preferred_tenant_id
                db.session.commit()

            self.logger.info(f"Customer created: {customer.full_name}")
            return True, [], customer

        except ValueError as e:
            return False, [str(e)], None
        except Exception as e:
            self.logger.error(f"Failed to create customer: {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None

    def update_customer(self, customer_id: int, customer_data: Dict[str, Any]) -> Tuple[bool, List[str], Optional[Customer]]:
        """
        Update customer information

        Args:
            customer_id: Customer ID
            customer_data: Updated customer data

        Returns:
            (success, error_messages, customer)
        """
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], None

            # Update attributes
            for key, value in customer_data.items():
                if hasattr(customer, key):
                    setattr(customer, key, value)

            # Validate data
            validation_errors = customer.validate()
            if validation_errors:
                db.session.rollback()
                return False, validation_errors, None

            # Save updates
            db.session.commit()

            # Keep any linked portal account aligned with the customer record.
            linked_user = User.find_by_email(customer.email)
            if linked_user and not linked_user.get_tenants() and linked_user.customer_id != customer.customer_id:
                linked_user.customer_id = customer.customer_id
                preferred_tenant_id = customer.preferred_tenant_id or customer.tenant_id
                if preferred_tenant_id:
                    linked_user.preferred_tenant_id = preferred_tenant_id
                db.session.commit()

            self.logger.info(f"Customer updated: {customer.full_name}")
            return True, [], customer

        except Exception as e:
            self.logger.error(f"Failed to update customer (ID: {customer_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None


    def get_customer_vehicles(self, customer_id: int) -> List[Vehicle]:
        """Get all vehicles for a customer."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return []
            return Vehicle.get_all_for_customer(customer_id)
        except Exception as e:
            self.logger.error(f"Failed to get customer vehicles (ID: {customer_id}): {e}")
            return []

    def get_vehicle_history(self, vehicle_id: int) -> List[Job]:
        """Get repair history for a vehicle."""
        try:
            vehicle = Vehicle.find_by_id(vehicle_id)
            if not vehicle:
                return []
            return vehicle.get_jobs()
        except Exception as e:
            self.logger.error(f"Failed to get vehicle history (ID: {vehicle_id}): {e}")
            return []

    def create_vehicle(self, vehicle_data: Dict[str, Any]) -> Tuple[bool, List[str], Optional[Vehicle]]:
        """Create a new vehicle for a customer."""
        try:
            # Normalise optional string fields: convert blank strings to None
            for field in ('registration_number', 'vin', 'color', 'notes'):
                if field in vehicle_data and isinstance(vehicle_data[field], str):
                    vehicle_data[field] = vehicle_data[field].strip() or None

            vehicle = Vehicle(**vehicle_data)
            errors = []
            if not vehicle.customer_id:
                errors.append("Customer is required")
            if not vehicle.make or not str(vehicle.make).strip():
                errors.append("Vehicle make is required")
            if not vehicle.model or not str(vehicle.model).strip():
                errors.append("Vehicle model is required")
            if errors:
                return False, errors, None

            # Keep vehicle inside the same tenant/workshop as its customer.
            customer = self.get_customer_by_id(vehicle.customer_id)
            if not customer:
                return False, ["Customer does not exist"], None

            resolved_tenant_id = customer.preferred_tenant_id or customer.tenant_id
            if not resolved_tenant_id:
                return False, ["Customer must belong to a workshop before a vehicle can be created"], None

            vehicle.tenant_id = resolved_tenant_id
            if vehicle.tenant_id != resolved_tenant_id:
                return False, ["Vehicle tenant could not be resolved safely"], None

            vehicle.save()
            if vehicle.is_primary:
                db.session.execute(
                    update(Vehicle)
                    .where(
                        Vehicle.customer_id == vehicle.customer_id,
                        Vehicle.vehicle_id != vehicle.vehicle_id,
                    )
                    .values(is_primary=False)
                )
                db.session.commit()
            self.logger.info(f"Vehicle created: {vehicle.display_name}")
            return True, [], vehicle
        except Exception as e:
            self.logger.error(f"Failed to create vehicle: {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None

    def update_vehicle(self, vehicle_id: int, vehicle_data: Dict[str, Any]) -> Tuple[bool, List[str], Optional[Vehicle]]:
        """Update an existing vehicle."""
        try:
            vehicle = Vehicle.find_by_id(vehicle_id)
            if not vehicle:
                return False, ["Vehicle does not exist"], None

            for key, value in vehicle_data.items():
                if hasattr(vehicle, key):
                    setattr(vehicle, key, value)

            if not vehicle.make or not str(vehicle.make).strip():
                return False, ["Vehicle make is required"], None
            if not vehicle.model or not str(vehicle.model).strip():
                return False, ["Vehicle model is required"], None

            if vehicle.is_primary:
                db.session.execute(
                    update(Vehicle)
                    .where(
                        Vehicle.customer_id == vehicle.customer_id,
                        Vehicle.vehicle_id != vehicle.vehicle_id,
                    )
                    .values(is_primary=False)
                )

            db.session.commit()
            self.logger.info(f"Vehicle updated: {vehicle.display_name}")
            return True, [], vehicle
        except Exception as e:
            self.logger.error(f"Failed to update vehicle (ID: {vehicle_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None

    def delete_vehicle(self, vehicle_id: int) -> Tuple[bool, List[str]]:
        """Delete a vehicle."""
        try:
            vehicle = Vehicle.find_by_id(vehicle_id)
            if not vehicle:
                return False, ["Vehicle does not exist"]
            if vehicle.get_jobs():
                return False, ["Cannot delete vehicle with repair history"]

            vehicle.delete()
            self.logger.info(f"Vehicle deleted: {vehicle_id}")
            return True, []
        except Exception as e:
            self.logger.error(f"Failed to delete vehicle (ID: {vehicle_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def delete_customer(self, customer_id: int) -> Tuple[bool, List[str]]:
        """
        Delete customer

        Args:
            customer_id: Customer ID

        Returns:
            (success, error_messages)
        """
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"]

            # Check for associated vehicles or jobs
            if customer.get_vehicles():
                return False, ["Cannot delete customer with vehicles"]
            jobs = customer.get_jobs()
            if jobs:
                return False, ["Cannot delete customer with work orders"]

            # Delete customer
            customer.delete()
            self.logger.info(f"Customer deleted: {customer.full_name}")
            return True, []

        except Exception as e:
            self.logger.error(f"Failed to delete customer (ID: {customer_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"]

    def get_customer_jobs(self, customer_id: int, completed_only: bool = False) -> List[Job]:
        """Get customer's work orders"""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return []

            return customer.get_jobs(completed_only)
        except Exception as e:
            self.logger.error(f"Failed to get customer jobs (ID: {customer_id}): {e}")
            return []

    def get_customer_unpaid_jobs(self, customer_id: int) -> List[Job]:
        """Get customer's unpaid orders"""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return []

            return customer.get_unpaid_jobs()
        except Exception as e:
            self.logger.error(f"Failed to get customer unpaid jobs (ID: {customer_id}): {e}")
            return []

    def get_customer_statistics(self, customer_id: int) -> Dict[str, Any]:
        """
        Get customer statistics

        Args:
            customer_id: Customer ID

        Returns:
            Customer statistics dictionary
        """
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return {}

            all_jobs = customer.get_jobs()
            unpaid_jobs = customer.get_unpaid_jobs()
            total_unpaid = customer.get_total_unpaid_amount()
            total_revenue = float(sum((job.total_cost or 0) for job in all_jobs))

            vehicles = customer.get_vehicles()
            return {
                'customer_info': customer.to_dict(),
                'total_jobs': len(all_jobs),
                'completed_jobs': len([j for j in all_jobs if j.completed]),
                'unpaid_jobs': len(unpaid_jobs),
                'vehicle_count': len(vehicles),
                'vehicles': [v.to_dict() for v in vehicles],
                'total_unpaid_amount': total_unpaid,
                'unpaid_amount': total_unpaid,
                'total_revenue': total_revenue,
                'recent_jobs': [j.to_dict() for j in all_jobs[:5]],
            }

        except Exception as e:
            self.logger.error(f"Failed to get customer statistics (ID: {customer_id}): {e}")
            return {}


    def set_customer_preferred_tenant(self, customer_id: int, tenant_id: int) -> Tuple[bool, List[str], Optional[Customer]]:
        """Persist a customer's preferred workshop and keep linked portal users aligned."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], None

            tenant = Tenant.find_by_id(tenant_id)
            if not tenant:
                return False, ["Selected workshop does not exist"], None

            customer.preferred_tenant_id = tenant.tenant_id

            linked_user = User.find_by_email(customer.email)
            if linked_user and getattr(linked_user, 'customer_id', None) == customer.customer_id:
                linked_user.preferred_tenant_id = tenant.tenant_id

            db.session.commit()
            return True, [], customer
        except Exception as e:
            self.logger.error(f"Failed to set preferred workshop (ID: {customer_id}): {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None

    def get_customer_dashboard_context(self, customer_id: int) -> Dict[str, Any]:
        """Build the data bundle used by the customer dashboard."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return {}

            today = date.today()
            all_jobs = customer.get_jobs()
            vehicles = customer.get_vehicles()
            unpaid_jobs = customer.get_unpaid_jobs()
            overdue_jobs = [job for job in unpaid_jobs if job.is_overdue]

            future_jobs = [job for job in all_jobs if job.job_date >= today and not job.completed]
            future_jobs = sorted(future_jobs, key=lambda job: (job.job_date, job.job_id))
            last_completed_job = next((job for job in all_jobs if job.completed), None)
            primary_vehicle = next((vehicle for vehicle in vehicles if vehicle.is_primary), vehicles[0] if vehicles else None)
            preferred_workshop = customer.preferred_tenant or (Tenant.find_by_id(customer.preferred_tenant_id) if customer.preferred_tenant_id else None)

            vehicle_histories: Dict[int, List[Job]] = {}
            vehicle_summaries: List[Dict[str, Any]] = []
            for vehicle in vehicles:
                history = customer.get_vehicle_history(vehicle.vehicle_id)
                vehicle_histories[vehicle.vehicle_id] = history
                latest_job = history[0] if history else None
                days_since_service = (today - latest_job.job_date).days if latest_job and latest_job.job_date else None
                if latest_job is None:
                    maintenance_status = 'No service history yet'
                elif days_since_service is not None and days_since_service >= 180:
                    maintenance_status = 'Service overdue'
                elif days_since_service is not None and days_since_service >= 90:
                    maintenance_status = 'Service due soon'
                else:
                    maintenance_status = 'Up to date'

                vehicle_summaries.append({
                    'vehicle': vehicle,
                    'history': history,
                    'history_count': len(history),
                    'last_service_job': latest_job,
                    'last_service_date': latest_job.job_date if latest_job else None,
                    'days_since_last_service': days_since_service,
                    'maintenance_status': maintenance_status,
                    'open_jobs': len([job for job in history if not job.completed]),
                })

            stats = self.get_customer_statistics(customer_id)
            stats['active_jobs'] = len([job for job in all_jobs if not job.completed])
            stats['upcoming_jobs'] = len(future_jobs)
            stats['last_service_date'] = last_completed_job.job_date if last_completed_job else None
            stats['last_service_vehicle'] = last_completed_job.vehicle_rel.display_name if last_completed_job and last_completed_job.vehicle_rel else None
            stats['preferred_workshop_name'] = preferred_workshop.name if preferred_workshop else None
            stats['preferred_workshop_slug'] = preferred_workshop.slug if preferred_workshop else None
            stats['primary_vehicle_name'] = primary_vehicle.display_name if primary_vehicle else None

            return {
                'stats': stats,
                'recent_jobs': all_jobs[:5],
                'upcoming_jobs': future_jobs[:5],
                'vehicle_histories': vehicle_histories,
                'vehicle_summaries': vehicle_summaries,
                'vehicles': vehicles,
                'unpaid_jobs': unpaid_jobs,
                'overdue_count': len(overdue_jobs),
                'active_jobs': stats['active_jobs'],
                'upcoming_jobs_count': len(future_jobs),
                'last_service_job': last_completed_job,
                'last_service_date': stats['last_service_date'],
                'last_service_vehicle': stats['last_service_vehicle'],
                'preferred_workshop': preferred_workshop,
                'primary_vehicle': primary_vehicle,
            }

        except Exception as e:
            self.logger.error(f"Failed to build customer dashboard context (ID: {customer_id}): {e}")
            return {}

    def get_customers_with_filter(
        self,
        has_unpaid: Optional[bool] = None,
        has_overdue: Optional[bool] = None,
    ) -> List[Customer]:
        """
        Get customers with optional billing filters.

        Args:
            has_unpaid: If True, only customers with unpaid jobs.
                        If False, only customers with no unpaid jobs.
            has_overdue: If True, only customers with overdue (>14 days unpaid) jobs.
                         If False, only customers with no overdue jobs.

        Returns:
            Filtered list of customers
        """
        try:
            customers = Customer.get_all_sorted()

            if has_unpaid is not None:
                if has_unpaid:
                    customers = [c for c in customers if c.get_unpaid_jobs()]
                else:
                    customers = [c for c in customers if not c.get_unpaid_jobs()]

            if has_overdue is not None:
                if has_overdue:
                    customers = [c for c in customers if c.has_overdue_bills()]
                else:
                    customers = [c for c in customers if not c.has_overdue_bills()]

            return customers

        except Exception as e:
            self.logger.error(f"Failed to filter customers: {e}")
            return []

    def schedule_job_for_customer(self, customer_id: int, job_date: date, vehicle_id: Optional[int] = None, mileage: Optional[int] = None, tenant_id: Optional[int] = None) -> Tuple[bool, List[str], Optional[int]]:
        """
        Schedule a work order for customer

        Args:
            customer_id: Customer ID
            job_date: Job date

        Returns:
            (success, error_messages, job_id)
        """
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False, ["Customer does not exist"], None

            if job_date < date.today():
                return False, ["Job date cannot be earlier than today"], None

            if vehicle_id:
                vehicle = Vehicle.find_by_id(vehicle_id)
                if not vehicle:
                    return False, ["Selected vehicle does not exist"], None
                if vehicle.customer_id != customer_id:
                    return False, ["Selected vehicle does not belong to the customer"], None
                if mileage is None:
                    mileage = vehicle.mileage

            if mileage is not None and mileage < 0:
                return False, ["Mileage cannot be negative"], None

            resolved_tenant_id = tenant_id or customer.preferred_tenant_id or customer.tenant_id
            if not resolved_tenant_id:
                return False, ["Please select a preferred workshop before scheduling a job"], None

            tenant = Tenant.find_by_id(resolved_tenant_id)
            if not tenant:
                return False, ["Selected workshop does not exist"], None

            customer.preferred_tenant_id = tenant.tenant_id
            linked_user = User.find_by_email(customer.email)
            if linked_user and getattr(linked_user, 'customer_id', None) == customer.customer_id:
                linked_user.preferred_tenant_id = tenant.tenant_id

            booking_service = JobService()
            conflicts = booking_service.detect_booking_conflicts(
                tenant_id=tenant.tenant_id,
                job_date=job_date,
                vehicle_id=vehicle_id,
            )
            if conflicts:
                return False, conflicts, None

            job = Job(
                job_date=job_date,
                customer=customer_id,
                tenant_id=tenant.tenant_id,
                vehicle_id=vehicle_id,
                mileage=mileage,
                total_cost=0.0,
                status=Job.STATUS_DRAFT,
                completed=False,
                paid=False,
            )
            job.save()
            job.set_status(Job.STATUS_DRAFT, note='Customer booked service request', initial=True)
            job.sync_vehicle_mileage()
            db.session.commit()

            self.logger.info(f"Scheduled job for customer {customer.full_name}")
            return True, [], job.job_id

        except Exception as e:
            self.logger.error(f"Failed to schedule job: {e}")
            db.session.rollback()
            return False, ["System error, please try again"], None
