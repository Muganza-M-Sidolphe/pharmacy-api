from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Medicine, StockBatch, TenantRole, UserTenant
from .tests import SubscriptionAccessTestMixin
from .utils.permission_codes import ALL_PERMISSION_CODES, user_permissions


@override_settings(SECURE_SSL_REDIRECT=False)
class UsersRolesAccessTests(TestCase, SubscriptionAccessTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.tenant = self.create_tenant("AccessPharm")
        self.owner = self.create_user("owner-access@example.com", "Owner")
        UserTenant.objects.create(user=self.owner, tenant=self.tenant, role="OWNER")
        self.cashier = self.create_user("cashier-access@example.com", "Cashier")
        UserTenant.objects.create(user=self.cashier, tenant=self.tenant, role="CASHIER")
        self.keeper = self.create_user("keeper-access@example.com", "Keeper")
        UserTenant.objects.create(user=self.keeper, tenant=self.tenant, role="STORE_KEEPER")

    def _url(self, name, *args):
        return reverse(name, args=args) + f"?tenantId={self.tenant.id}"

    def _as(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def test_owner_lists_users_roles_and_permissions(self):
        client = self._as(self.owner)
        users = client.get(self._url("access-users")).data["results"]
        self.assertEqual({u["role"] for u in users}, {"Owner", "Cashier", "Storekeeper"})
        roles = client.get(self._url("access-roles")).data["results"]
        self.assertEqual([r["id"] for r in roles], ["owner", "pharmacist", "accountant", "store_keeper", "cashier"])
        self.assertEqual(roles[0]["permissions"], ALL_PERMISSION_CODES)
        groups = client.get(self._url("access-permissions")).data["results"]
        self.assertEqual(groups[0]["name"], "Medicine")

    def test_customizing_a_system_role_changes_its_users_access(self):
        res = self._as(self.owner).patch(
            self._url("access-role-detail", "cashier"),
            {"permissions": ["sales.view", "sales.create", "inventory.adjust"]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        # inventory.adjust pulls in its dependency inventory.view
        self.assertEqual(res.data["permissions"], ["inventory.view", "inventory.adjust", "sales.view", "sales.create"])
        self.assertIn("inventory.adjust", user_permissions(self.cashier, self.tenant.id))

        # ... and the backend now lets the cashier adjust stock.
        batch = StockBatch.objects.create(
            medicine=Medicine.objects.create(tenant=self.tenant, brand_name="Amoxil"), batch_number="B1", quantity=5
        )
        res = self._as(self.cashier).post(
            self._url("stock-batch-adjust", batch.id), {"quantity": 1, "reason": "Count"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_owner_role_cannot_be_customized_or_assigned(self):
        client = self._as(self.owner)
        self.assertEqual(client.patch(self._url("access-role-detail", "owner"), {"permissions": []}, format="json").status_code, 403)
        self.assertEqual(client.post(self._url("access-user-role", self.cashier.id), {"role": "Owner"}, format="json").status_code, 403)
        self.assertEqual(client.post(self._url("access-user-role", self.owner.id), {"role": "Cashier"}, format="json").status_code, 403)

    def test_custom_role_create_and_assign(self):
        client = self._as(self.owner)
        res = client.post(self._url("access-roles"), {
            "name": "Senior Pharmacist",
            "description": "Pharmacist who can also adjust stock.",
            "permissions": ["medicine.view", "medicine.add", "medicine.import", "inventory.adjust", "orders.approve"],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["baseRole"], "PHARMACIST")
        self.assertEqual(client.post(self._url("access-roles"), {"name": "senior pharmacist"}, format="json").status_code, 400)
        self.assertEqual(client.post(self._url("access-roles"), {"name": "Cashier"}, format="json").status_code, 400)

        res = client.post(self._url("access-user-role", self.cashier.id), {"role": "Senior Pharmacist"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["role"], res.data["baseRole"]), ("Senior Pharmacist", "PHARMACIST"))
        membership = UserTenant.objects.get(user=self.cashier, tenant=self.tenant)
        self.assertEqual(membership.role, "PHARMACIST")
        self.assertIn("inventory.adjust", user_permissions(self.cashier, self.tenant.id))

        # Back to a system role clears the custom role.
        res = client.post(self._url("access-user-role", self.cashier.id), {"role": "Cashier"}, format="json")
        self.assertEqual((res.data["role"], res.data["roleId"]), ("Cashier", "cashier"))

    def test_user_permission_overrides(self):
        client = self._as(self.owner)
        url = self._url("access-user-permissions", self.cashier.id)
        res = client.put(url, {"permissions": ["sales.view"]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["hasCustomPermissions"])
        self.assertEqual(client.get(url).data["permissions"], ["sales.view"])
        self.assertEqual(client.put(url, {"permissions": ["made.up"]}, format="json").status_code, 400)

        # Login carries the user's own permissions.
        res = self.client.post(reverse("login"), {"email": "cashier-access@example.com", "password": "pass1234"}, format="json")
        self.assertEqual(res.data["data"]["permissions"], ["sales.view"])

        client.force_authenticate(user=self.owner)
        res = client.put(url, {"permissions": None}, format="json")
        self.assertFalse(res.data["hasCustomPermissions"])
        self.assertIn("sales.create", res.data["permissions"])

    def test_users_without_access_are_refused(self):
        client = self._as(self.cashier)
        self.assertEqual(client.get(self._url("access-users")).status_code, 403)
        self.assertEqual(client.get(self._url("access-roles")).status_code, 403)

    def test_delegated_manager_cannot_escalate(self):
        UserTenant.objects.filter(user=self.keeper).update(
            permission_overrides=["users.view", "users.manage_permissions", "inventory.view", "inventory.adjust"]
        )
        client = self._as(self.keeper)
        url = self._url("access-user-permissions", self.cashier.id)
        self.assertEqual(client.put(url, {"permissions": ["inventory.adjust"]}, format="json").status_code, 200)
        self.assertEqual(client.put(url, {"permissions": ["users.edit"]}, format="json").status_code, 403)
        own = self._url("access-user-permissions", self.keeper.id)
        self.assertEqual(client.put(own, {"permissions": ["inventory.view"]}, format="json").status_code, 403)

    def test_edit_user_details(self):
        client = self._as(self.owner)
        url = self._url("access-user-detail", self.cashier.id)
        res = client.patch(url, {"name": "Cashier One", "email": "c1@example.com", "phone": "+250788000111"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["name"], res.data["email"], res.data["phone"]), ("Cashier One", "c1@example.com", "+250788000111"))
        self.assertEqual(client.patch(url, {"email": "keeper-access@example.com"}, format="json").status_code, 400)

    @override_settings(DEMO_ROLE_SWITCH_EMAILS={"keeper-access@example.com"})
    def test_demo_switch_resets_custom_access(self):
        role = TenantRole.objects.create(tenant=self.tenant, name="Lead", base_role="STORE_KEEPER", permissions=["sales.view"])
        UserTenant.objects.filter(user=self.keeper).update(custom_role=role, permission_overrides=["sales.view"])
        res = self._as(self.keeper).post(
            reverse("switch-role"), {"tenantId": str(self.tenant.id), "role": "STORE_KEEPER"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("inventory.adjust", res.data["data"]["permissions"])
