from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from .models import User, Tenant, UserTenant, Notification
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
