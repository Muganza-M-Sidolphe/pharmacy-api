from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from django.test.utils import override_settings
from django.utils import timezone
from .models import User, Tenant, UserTenant, Notification, TenantSubscription
from decimal import Decimal
import uuid


class NotificationsAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create(email="owner@example.com", name="Owner", password="pass")
        self.tenant = Tenant.objects.create(name="TestPharm", email="test@pharm.com", phone="123", address="x", license_number="L123")
        UserTenant.objects.create(user=self.owner, tenant=self.tenant, role="OWNER")

    def test_create_and_list_notifications(self):
        # authenticate as owner
        self.client.force_authenticate(user=self.owner)

        # create notification
        res = self.client.post(reverse('owner-notifications'), data={
            "tenantId": str(self.tenant.id),
            "title": "Stock low",
            "message": "Some items are low"
        }, format='json')
        self.assertEqual(res.status_code, 201)

        # list notifications
        res = self.client.get(reverse('owner-notifications') + f"?tenantId={self.tenant.id}")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(len(res.data['results']) >= 1)


class CashierInventoryAPITestCase(TestCase):
    def setUp(self):
        from .models import Medicine, StockBatch
        self.client = APIClient()
        self.cashier = User.objects.create(email="cashier@example.com", name="Cashier", password="pass")
        self.tenant = Tenant.objects.create(name="InvPharm", email="inv@pharm.com", phone="321", address="y", license_number="L999")
        UserTenant.objects.create(user=self.cashier, tenant=self.tenant, role="STAFF")

        # create a medicine and a stock batch
        self.medicine = Medicine.objects.create(tenant=self.tenant, brand_name="Panadol", generic_name="Paracetamol")
        StockBatch.objects.create(medicine=self.medicine, batch_number="B1", quantity=25, purchase_price=Decimal('1.00'), selling_price=Decimal('1.50'))

    def test_cashier_can_list_inventory(self):
        self.client.force_authenticate(user=self.cashier)
        url = reverse('cashier-inventory') + f"?tenantId={self.tenant.id}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('results', res.data)
        self.assertTrue(len(res.data['results']) >= 1)
        first = res.data['results'][0]
        self.assertEqual(first['brandName'], 'Panadol')
        self.assertEqual(first['totalStock'], 25)


class SubscriptionAccessTestMixin:
    def create_user(self, email, name, department="WHOLESALE"):
        user = User.objects.create(email=email, name=name, department=department)
        user.set_password("pass1234")
        user.save()
        return user

    def create_tenant(self, name, email="tenant@example.com"):
        suffix = uuid.uuid4().hex[:6]
        return Tenant.objects.create(
            name=name,
            email=f"{suffix}-{email}",
            phone="123456789",
            address="Kigali",
            license_number=f"LIC-{suffix}",
        )

    def attach_subscription(self, tenant, plan_id, status="ACTIVE", **extra_fields):
        defaults = {
            "billing_cycle": "monthly",
            "subscription_start_date": timezone.now().date(),
            "subscription_end_date": timezone.now().date() + timezone.timedelta(days=30),
        }
        defaults.update(extra_fields)
        return TenantSubscription.objects.create(
            tenant=tenant,
            plan_id=plan_id,
            status=status,
            **defaults,
        )


class SubscriptionAccessEndpointTests(TestCase, SubscriptionAccessTestMixin):
    def setUp(self):
        self.client = APIClient()

    def test_owner_reports_blocked_for_starter_plan(self):
        owner = self.create_user("starter-owner@example.com", "Starter Owner")
        tenant = self.create_tenant("Starter Pharmacy")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        self.attach_subscription(tenant, "starter")

        self.client.force_authenticate(user=owner)
        response = self.client.get(
            reverse("owner-reports-dashboard"),
            {"tenantId": str(tenant.id)},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("advanced_reports", response.data["detail"])

    def test_owner_reports_allowed_for_growth_plan(self):
        owner = self.create_user("growth-owner@example.com", "Growth Owner")
        tenant = self.create_tenant("Growth Pharmacy")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        self.attach_subscription(tenant, "growth")

        self.client.force_authenticate(user=owner)
        response = self.client.get(
            reverse("owner-reports-dashboard"),
            {"tenantId": str(tenant.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.data)

    def test_cashier_inventory_blocked_for_expired_subscription(self):
        cashier = self.create_user("expired-cashier@example.com", "Expired Cashier")
        tenant = self.create_tenant("Expired Pharmacy")
        UserTenant.objects.create(user=cashier, tenant=tenant, role="CASHIER")
        self.attach_subscription(
            tenant,
            "starter",
            status="EXPIRED",
            subscription_end_date=timezone.now().date() - timezone.timedelta(days=1),
        )

        self.client.force_authenticate(user=cashier)
        response = self.client.get(
            reverse("cashier-inventory"),
            {"tenantId": str(tenant.id)},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Subscription", response.data["detail"])

    def test_collaborative_retail_requests_require_enterprise_wholesale_plan(self):
        collaborator = self.create_user(
            "retail-collab@example.com",
            "Retail Collaborator",
            department="RETAIL",
        )
        owner = self.create_user("wholesale-owner@example.com", "Wholesale Owner")
        tenant = self.create_tenant("Wholesale Collaboration Tenant")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        UserTenant.objects.create(user=collaborator, tenant=tenant, role="PHARMACIST")

        self.attach_subscription(tenant, "growth")
        self.client.force_authenticate(user=collaborator)
        blocked_response = self.client.get(
            reverse("retail-wholesale-requests"),
            {"tenantId": str(tenant.id)},
        )

        self.assertEqual(blocked_response.status_code, 403)

        tenant.subscription.delete()
        self.attach_subscription(tenant, "enterprise")

        allowed_response = self.client.get(
            reverse("retail-wholesale-requests"),
            {"tenantId": str(tenant.id)},
        )

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual(allowed_response.data, [])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubscriptionLimitTests(TestCase, SubscriptionAccessTestMixin):
    def setUp(self):
        self.client = APIClient()

    def test_create_user_enforces_plan_user_limit(self):
        owner = self.create_user("owner-limit@example.com", "Owner Limit")
        tenant = self.create_tenant("Starter Limited Pharmacy")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        self.attach_subscription(tenant, "starter")

        for index in range(4):
            member = self.create_user(f"member{index}@example.com", f"Member {index}")
            UserTenant.objects.create(user=member, tenant=tenant, role="CASHIER")

        self.client.force_authenticate(user=owner)
        response = self.client.post(
            reverse("create-user"),
            data={
                "tenantId": str(tenant.id),
                "name": "Blocked User",
                "email": "blocked-user@example.com",
                "department": "WHOLESALE",
                "role": "CASHIER",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("only 5 users", response.data["error"])
