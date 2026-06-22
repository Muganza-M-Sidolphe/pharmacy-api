from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from django.test.utils import override_settings
from django.utils import timezone
from .models import User, Tenant, UserTenant, Notification, TenantSubscription
from decimal import Decimal
import uuid


@override_settings(SECURE_SSL_REDIRECT=False)
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


@override_settings(SECURE_SSL_REDIRECT=False)
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


@override_settings(SECURE_SSL_REDIRECT=False)
class RetailExpensesAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cashier = User.objects.create(
            email="retail-expense@example.com",
            name="Retail Expense",
            password="pass",
            department="RETAIL",
        )
        self.tenant = Tenant.objects.create(
            name="ExpensePharm",
            email="expense@pharm.com",
            phone="321",
            address="Kigali",
            license_number="EXP-001",
        )
        UserTenant.objects.create(user=self.cashier, tenant=self.tenant, role="PHARMACIST")

    def test_create_expense_returns_created_response_with_camel_case_date(self):
        self.client.force_authenticate(user=self.cashier)
        response = self.client.post(
            reverse("retails-expenses"),
            {
                "category": "Transport",
                "description": "Delivery fuel",
                "amount": "1200.50",
                "expenseDate": "2026-06-22",
            },
            format="json",
            QUERY_STRING=f"tenantId={self.tenant.id}",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["category"], "Transport")
        self.assertEqual(response.data["data"]["amount"], 1200.5)
        self.assertEqual(response.data["data"]["expense_date"], "2026-06-22")

    def test_create_expense_rejects_invalid_date_before_insert(self):
        self.client.force_authenticate(user=self.cashier)
        response = self.client.post(
            reverse("retails-expenses"),
            {
                "category": "Transport",
                "amount": "1200.50",
                "expenseDate": "22-06-2026",
            },
            format="json",
            QUERY_STRING=f"tenantId={self.tenant.id}",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["message"], "expense_date must be in YYYY-MM-DD format")


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


@override_settings(SECURE_SSL_REDIRECT=False)
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

    def test_retail_report_includes_print_report_sections(self):
        from .models import Expense, ExpenseCategory, Medicine, Sale, SaleItem, StockBatch

        cashier = self.create_user("retail-report@example.com", "Retail Reporter", department="RETAIL")
        tenant = self.create_tenant("Pharmalinkr Pharmacy")
        UserTenant.objects.create(user=cashier, tenant=tenant, role="PHARMACIST")
        self.attach_subscription(tenant, "growth")

        medicine = Medicine.objects.create(
            tenant=tenant,
            created_by=cashier,
            brand_name="Paracetamol 500mg",
            category="Prescription Medicines",
        )
        batch = StockBatch.objects.create(
            medicine=medicine,
            created_by=cashier,
            batch_number="B1",
            quantity=20,
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("1200.00"),
            expiry_date=timezone.now().date() - timezone.timedelta(days=1),
        )
        sale = Sale.objects.create(
            tenant=tenant,
            cashier=cashier,
            invoice_number="RPT-001",
            customer_name="Walk-in Customer",
            status="COMPLETED",
            subtotal=Decimal("2400.00"),
            total_amount=Decimal("2400.00"),
            paid_amount=Decimal("2400.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            medicine=medicine,
            batch=batch,
            quantity=2,
            unit_price=Decimal("1200.00"),
            subtotal=Decimal("2400.00"),
        )
        category = ExpenseCategory.objects.create(tenant=tenant, name="Rent")
        Expense.objects.create(
            tenant=tenant,
            category=category,
            amount=Decimal("500.00"),
            expense_date=timezone.now().date(),
            created_by=cashier,
        )

        self.client.force_authenticate(user=cashier)
        response = self.client.get(reverse("retails-reports"), {"tenantId": str(tenant.id)})

        self.assertEqual(response.status_code, 200)
        report = response.data["data"]["printReport"]
        self.assertEqual(report["title"], "Financial Performance Report")
        self.assertEqual(report["branding"]["name"], "Pharmalinkr Pharmacy")
        self.assertEqual(report["summary"]["totalRevenue"], "2400.00")
        self.assertEqual(report["summary"]["netProfit"], "1900.00")
        self.assertEqual(
            [section["title"] for section in report["sections"]],
            [
                "Revenue Breakdown (by Category)",
                "Expense Breakdown (by Type)",
                "Profit Analysis",
                "Top Selling Medicines",
                "Stock Loss Report",
            ],
        )

    def test_retail_report_download_returns_pdf(self):
        cashier = self.create_user("retail-report-pdf@example.com", "Retail PDF", department="RETAIL")
        tenant = self.create_tenant("Retail PDF Pharmacy")
        UserTenant.objects.create(user=cashier, tenant=tenant, role="PHARMACIST")
        self.attach_subscription(tenant, "growth")

        self.client.force_authenticate(user=cashier)
        response = self.client.get(reverse("retails-reports-download"), {"tenantId": str(tenant.id)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_accountant_financial_report_includes_print_report_sections(self):
        from .models import Expense, ExpenseCategory, Medicine, Sale, SaleItem, StockBatch

        accountant = self.create_user("accountant-report@example.com", "Accountant Reporter")
        owner = self.create_user("accountant-owner@example.com", "Owner")
        tenant = self.create_tenant("Wholesale Report Pharmacy")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        UserTenant.objects.create(user=accountant, tenant=tenant, role="ACCOUNTANT")
        self.attach_subscription(tenant, "growth")

        medicine = Medicine.objects.create(tenant=tenant, brand_name="Amoxicillin 500mg", category="OTC Medicines")
        batch = StockBatch.objects.create(
            medicine=medicine,
            batch_number="B2",
            quantity=30,
            purchase_price=Decimal("300.00"),
            selling_price=Decimal("800.00"),
        )
        sale = Sale.objects.create(
            tenant=tenant,
            cashier=accountant,
            invoice_number="ACC-001",
            customer_name="Clinic",
            status="APPROVED",
            subtotal=Decimal("1600.00"),
            total_amount=Decimal("1600.00"),
            paid_amount=Decimal("1600.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            medicine=medicine,
            batch=batch,
            quantity=2,
            unit_price=Decimal("800.00"),
            subtotal=Decimal("1600.00"),
        )
        category = ExpenseCategory.objects.create(tenant=tenant, name="Utilities")
        Expense.objects.create(
            tenant=tenant,
            category=category,
            amount=Decimal("200.00"),
            expense_date=timezone.now().date(),
            created_by=accountant,
        )

        self.client.force_authenticate(user=accountant)
        response = self.client.get(reverse("accountant-reports-financial"), {"tenantId": str(tenant.id)})

        self.assertEqual(response.status_code, 200)
        report = response.data["printReport"]
        self.assertEqual(report["branding"]["name"], "Wholesale Report Pharmacy")
        self.assertEqual(report["summary"]["totalRevenue"], "1600.00")
        self.assertEqual(report["summary"]["netProfit"], "1400.00")
        self.assertEqual(report["sections"][0]["rows"][0]["category"], "OTC Medicines")

    def test_accountant_financial_report_download_returns_pdf(self):
        accountant = self.create_user("accountant-report-pdf@example.com", "Accountant PDF")
        owner = self.create_user("accountant-owner-pdf@example.com", "Owner")
        tenant = self.create_tenant("Accountant PDF Pharmacy")
        UserTenant.objects.create(user=owner, tenant=tenant, role="OWNER")
        UserTenant.objects.create(user=accountant, tenant=tenant, role="ACCOUNTANT")
        self.attach_subscription(tenant, "growth")

        self.client.force_authenticate(user=accountant)
        response = self.client.get(reverse("accountant-reports-financial-download"), {"tenantId": str(tenant.id)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@override_settings(SECURE_SSL_REDIRECT=False)
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
