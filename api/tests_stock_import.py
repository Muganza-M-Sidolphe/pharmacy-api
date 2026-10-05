import io
from datetime import date, datetime
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from openpyxl import Workbook
from rest_framework.test import APIClient

from .models import Medicine, StockBatch, StockMovement, UserTenant
from .tests import SubscriptionAccessTestMixin
from .utils.medicine_import import parse_date, parse_decimal, parse_medicine_name


def _text_pdf(lines):
    """A one-page PDF with each cell drawn at a fixed column position, like an exported report."""
    content = "".join(
        f"BT /F1 10 Tf {40 + col * 110} {760 - row * 16} Td ({cell}) Tj ET\n"
        for row, cells in enumerate(lines)
        for col, cell in enumerate(cells)
        if cell
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    pdf, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin-1")


@override_settings(SECURE_SSL_REDIRECT=False)
class StockBatchEndpointTests(TestCase, SubscriptionAccessTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.tenant = self.create_tenant("StockPharm")
        self.owner = self.create_user("owner-stock@example.com", "Owner")
        UserTenant.objects.create(user=self.owner, tenant=self.tenant, role="OWNER")
        self.keeper = self.create_user("keeper@example.com", "Keeper")
        UserTenant.objects.create(user=self.keeper, tenant=self.tenant, role="STORE_KEEPER")
        self.cashier = self.create_user("cashier-stock@example.com", "Cashier")
        UserTenant.objects.create(user=self.cashier, tenant=self.tenant, role="CASHIER")
        self.medicine = Medicine.objects.create(tenant=self.tenant, brand_name="Amoxil")
        self.batch = StockBatch.objects.create(medicine=self.medicine, batch_number="B1", quantity=10)

    def _post(self, url, data, user=None):
        self.client.force_authenticate(user=user or self.keeper)
        return self.client.post(f"{url}?tenantId={self.tenant.id}", data, format="json")

    def test_receive_creates_new_batch_and_movement(self):
        res = self._post("/api/stock/in", {
            "medicineId": str(self.medicine.id), "batchNumber": "B2", "quantity": 25,
            "purchasePrice": 100, "sellingPrice": 150, "expiryDate": "2030-01-31",
        })
        self.assertEqual(res.status_code, 201, res.data)
        batch = StockBatch.objects.get(medicine=self.medicine, batch_number="B2")
        self.assertEqual(batch.quantity, 25)
        self.assertEqual(StockMovement.objects.get(batch=batch).movement_type, "RECEIVE")

    def test_receive_rejects_existing_batch_number(self):
        res = self._post("/api/stock/in/", {"medicineId": str(self.medicine.id), "batchNumber": "b1", "quantity": 5})
        self.assertEqual(res.status_code, 400)

    def test_adjust_and_movement_history(self):
        url = reverse("stock-batch-adjust", args=[self.batch.id])
        res = self._post(url, {"quantity": -3, "reason": "Damaged"})
        self.assertEqual(res.status_code, 200, res.data)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 7)

        self.assertEqual(self._post(url, {"quantity": -8, "reason": "Too many"}).status_code, 400)
        self.assertEqual(self._post(url, {"quantity": 2, "reason": ""}).status_code, 400)

        self.client.force_authenticate(user=self.keeper)
        res = self.client.get(reverse("stock-batch-movements", args=[self.batch.id]) + f"?tenantId={self.tenant.id}")
        self.assertEqual(res.status_code, 200)
        movement = res.data["results"][0]
        self.assertEqual((movement["type"], movement["quantity"], movement["balance_after"]), ("ADJUSTMENT", -3, 7))
        self.assertEqual(movement["userName"], "Keeper")

    def test_transfer_reduces_stock(self):
        url = reverse("stock-batch-transfer", args=[self.batch.id])
        res = self._post(url, {"quantity": 4, "destination": "Branch 2", "reference": "TR-1"})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["batch"]["quantity"], 6)
        self.assertEqual(self._post(url, {"quantity": 7, "destination": "Branch 2"}).status_code, 400)
        self.assertEqual(self._post(url, {"quantity": 1, "destination": ""}).status_code, 400)

    def test_cashier_cannot_adjust(self):
        res = self._post(reverse("stock-batch-adjust", args=[self.batch.id]), {"quantity": 1, "reason": "x"}, user=self.cashier)
        self.assertEqual(res.status_code, 403)

    def test_batch_of_other_pharmacy_is_not_found(self):
        other = self.create_tenant("Other")
        other_batch = StockBatch.objects.create(
            medicine=Medicine.objects.create(tenant=other, brand_name="X"), batch_number="O1", quantity=5
        )
        res = self._post(reverse("stock-batch-adjust", args=[other_batch.id]), {"quantity": 1, "reason": "x"})
        self.assertEqual(res.status_code, 404)

    def test_login_carries_role_permissions(self):
        res = self.client.post(reverse("login"), {"email": "keeper@example.com", "password": "pass1234"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("inventory.adjust", res.data["data"]["permissions"])
        self.assertNotIn("users.view", res.data["data"]["permissions"])


@override_settings(SECURE_SSL_REDIRECT=False)
class MedicineImportTests(TestCase, SubscriptionAccessTestMixin):
    CSV = (
        "Inventory export\n"
        "Drug Name,Generic,Batch No,Expiry Date,Qty,Unit Cost,Price (RWF)\n"
        "Panadol,Paracetamol,P-1,31/12/2030,100,50,\n"
        "Panadol,Paracetamol,P-2,06/2031,40,55,90\n"
        "Amoxil,Amoxicillin,,2031-01-15,20,200,300\n"
        "Bad Row,,X-1,not a date,ten,1,2\n"
    )

    def setUp(self):
        self.client = APIClient()
        self.tenant = self.create_tenant("ImportPharm")
        self.owner = self.create_user("owner-import@example.com", "Owner")
        UserTenant.objects.create(user=self.owner, tenant=self.tenant, role="OWNER")
        self.client.force_authenticate(user=self.owner)

    def _url(self, name, *args):
        return reverse(name, args=args) + f"?tenantId={self.tenant.id}"

    def _upload(self, content=None, name="stock.csv"):
        content = self.CSV if content is None else content
        data = content.encode() if isinstance(content, str) else content
        upload = SimpleUploadedFile(name, data)
        return self.client.post(self._url("medicine-import-sessions"), {"file": upload}, format="multipart")

    def _preview(self, session_id):
        res = self.client.get(self._url("medicine-import-preview", session_id))
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def _map(self, session_id, columns, **extra):
        payload = {
            "mapping": [{"source_column": c["name"], "target_field": c["target_field"]} for c in columns],
            "pricing": {"method": "markup", "markup_percent": 20},
            "missing_batch_number_strategy": "manual",
        }
        payload.update(extra)
        return self.client.patch(self._url("medicine-import-mapping", session_id), payload, format="json")

    def test_full_import_flow(self):
        res = self._upload()
        self.assertEqual(res.status_code, 201, res.data)
        session_id = res.data["session_id"]
        self.assertEqual(res.data["rows_count"], 4)

        res = self.client.post(self._url("medicine-import-analyze", session_id))
        targets = {c["name"]: c["target_field"] for c in res.data["detected_columns"]}
        self.assertEqual(targets, {
            "Drug Name": "medicine_name", "Generic": "generic_name", "Batch No": "batch_number",
            "Expiry Date": "expiry_date", "Qty": "quantity", "Unit Cost": "cost_price",
            "Price (RWF)": "selling_price",
        })
        columns = res.data["detected_columns"]

        self.assertEqual(self._map(session_id, columns).status_code, 200)
        preview = self._preview(session_id)
        records = {r["row_number"]: r for r in preview["records"]}
        # Row numbers are spreadsheet rows: title on row 1, header on row 2.
        self.assertEqual(records[3]["mapped_data"]["selling_price"], "60.00")
        self.assertEqual(records[3]["selling_price_source"], "markup")
        self.assertEqual(records[4]["mapped_data"]["expiry_date"], "2031-06-30")
        self.assertIn("Batch number is missing.", records[5]["errors"])
        self.assertFalse(records[6]["is_valid"])
        self.assertEqual(preview["summary"]["valid"], 2)

        # Generate the missing batch number and fix the bad row by hand.
        self._map(session_id, columns, missing_batch_number_strategy="generate")
        res = self.client.patch(
            self._url("medicine-import-record", session_id, records[6]["id"]),
            {"quantity": "10", "expiry_date": "2032-02-01"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        preview = self._preview(session_id)
        records = {r["row_number"]: r for r in preview["records"]}
        self.assertTrue(records[5]["batch_number_generated"])
        self.assertRegex(records[5]["mapped_data"]["batch_number"], r"^AUTO-\d{8}-\d{8}$")
        self.assertTrue(records[6]["is_valid"], records[6]["errors"])
        self.assertEqual(preview["summary"]["new_medicines"], 3)

        res = self.client.post(self._url("medicine-import-confirm", session_id))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["new_medicines"], res.data["batches_created"]), (3, 4))
        panadol = Medicine.objects.get(tenant=self.tenant, brand_name="Panadol")
        self.assertEqual(panadol.batches.count(), 2)
        self.assertEqual(panadol.batches.get(batch_number="P-1").selling_price, Decimal("60.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type="IMPORT").count(), 4)

        self.assertEqual(self.client.post(self._url("medicine-import-confirm", session_id)).status_code, 409)

    def test_existing_medicine_and_duplicate_batches(self):
        existing = Medicine.objects.create(tenant=self.tenant, brand_name="Panadol")
        StockBatch.objects.create(medicine=existing, batch_number="P-1", quantity=1)
        csv_text = "Medicine,Batch,Quantity,Selling Price\npanadol,P-1,5,10\nPanadol,P-9,5,10\nPanadol,P-9,6,10\n"
        session_id = self._upload(csv_text).data["session_id"]
        rows = self._preview(session_id)["records"]
        self.assertIn("already exists", rows[0]["errors"][0])
        self.assertTrue(rows[1]["is_valid"])
        self.assertEqual(rows[1]["medicine_status"], "existing")
        self.assertIn("repeated", rows[2]["errors"][0])

        res = self.client.post(self._url("medicine-import-confirm", session_id))
        self.assertEqual((res.data["updated_medicines"], res.data["new_medicines"], res.data["skipped"]), (1, 0, 2))
        self.assertEqual(Medicine.objects.filter(tenant=self.tenant).count(), 1)

    def test_remove_record(self):
        session_id = self._upload("Medicine,Batch,Quantity,Selling Price\nA,1,5,10\nB,,5,10\n").data["session_id"]
        bad = self._preview(session_id)["records"][1]
        res = self.client.delete(self._url("medicine-import-record", session_id, bad["id"]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._preview(session_id)["summary"]["total"], 1)

    def test_xlsx_upload(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Product", "Lot", "Expiry", "Stock", "Selling Price"])
        sheet.append(["Coartem", "C-1", datetime(2031, 3, 1), 12, 1500.0])
        buffer = io.BytesIO()
        workbook.save(buffer)
        session_id = self._upload(buffer.getvalue(), name="stock.xlsx").data["session_id"]
        record = self._preview(session_id)["records"][0]
        self.assertTrue(record["is_valid"], record["errors"])
        self.assertEqual(record["mapped_data"]["expiry_date"], "2031-03-01")
        self.assertEqual(record["mapped_data"]["selling_price"], "1500.00")

    def test_text_pdf_upload(self):
        pdf = _text_pdf([
            ["Medicine", "Batch", "Expiry", "Qty", "Selling Price"],
            ["Panadol 500mg", "P-1", "12/2030", "100", "60"],
            ["Coartem", "", "2031-03-01", "12", "1,500"],
        ])
        res = self._upload(pdf, name="report.pdf")
        self.assertEqual(res.status_code, 201, res.data)
        rows = self._preview(res.data["session_id"])["records"]
        self.assertEqual(rows[0]["mapped_data"]["medicine_name"], "Panadol 500mg")
        self.assertEqual(rows[0]["mapped_data"]["expiry_date"], "2030-12-31")
        self.assertEqual(rows[1]["mapped_data"]["batch_number"], "")
        self.assertEqual(rows[1]["mapped_data"]["selling_price"], "1500.00")

    def test_scanned_pdf_gives_clear_error(self):
        res = self._upload(_text_pdf([]), name="scan.pdf")
        self.assertEqual(res.status_code, 400)
        self.assertIn("scanned", res.data["detail"])

    def test_rejects_unsupported_file(self):
        self.assertEqual(self._upload("hello", name="notes.txt").status_code, 400)

    def test_cashier_cannot_import_and_sessions_are_private(self):
        session_id = self._upload().data["session_id"]
        cashier = self.create_user("cashier-import@example.com", "Cashier")
        UserTenant.objects.create(user=cashier, tenant=self.tenant, role="CASHIER")
        keeper = self.create_user("keeper-import@example.com", "Keeper")
        UserTenant.objects.create(user=keeper, tenant=self.tenant, role="STORE_KEEPER")

        self.client.force_authenticate(user=cashier)
        self.assertEqual(self._upload().status_code, 403)
        self.client.force_authenticate(user=keeper)
        self.assertEqual(self.client.get(self._url("medicine-import-preview", session_id)).status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class SmartColumnDetectionTests(TestCase, SubscriptionAccessTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.tenant = self.create_tenant("SmartPharm")
        self.owner = self.create_user("owner-smart@example.com", "Owner")
        UserTenant.objects.create(user=self.owner, tenant=self.tenant, role="OWNER")
        self.client.force_authenticate(user=self.owner)

    def _url(self, name, *args):
        return reverse(name, args=args) + f"?tenantId={self.tenant.id}"

    def _analyze(self, csv_text):
        upload = SimpleUploadedFile("stock.csv", csv_text.encode())
        session_id = self.client.post(self._url("medicine-import-sessions"), {"file": upload}, format="multipart").data["session_id"]
        res = self.client.post(self._url("medicine-import-analyze", session_id))
        return session_id, {c["name"]: c["target_field"] for c in res.data["detected_columns"]}

    def test_french_headers(self):
        _, targets = self._analyze(
            "Désignation;DCI;N° Lot;Date de péremption;Qté;Prix d'achat;Prix de vente;Fournisseur\n"
            "Doliprane 500mg cp;Paracétamol;L1;12/2030;10;100;150;Sopharma\n"
        )
        self.assertEqual(targets, {
            "Désignation": "medicine_name", "DCI": "generic_name", "N° Lot": "batch_number",
            "Date de péremption": "expiry_date", "Qté": "quantity", "Prix d'achat": "cost_price",
            "Prix de vente": "selling_price", "Fournisseur": "supplier",
        })

    def test_unknown_headers_guessed_from_values(self):
        _, targets = self._analyze(
            "Col A,Col B,Col C,Col D,Col E,Col F\n"
            "Amoxil 500mg Caps,AX23K,2024-01-10,2027-01-31,120,1500\n"
            "Coartem 20/120mg,CT9921,2024-03-02,2026-11-30,40,4200\n"
            "Panadol Syrup,PN-77B,2024-05-01,2027-05-31,15,2500\n"
        )
        self.assertEqual(targets, {
            "Col A": "medicine_name", "Col B": "batch_number", "Col C": "manufacturing_date",
            "Col D": "expiry_date", "Col E": "quantity", "Col F": "selling_price",
        })

    def test_strength_and_form_are_read_from_the_name(self):
        session_id, _ = self._analyze("Medicine,Batch,Qty,Selling Price\nAmoxil 500 mg Caps,B1,5,10\n")
        data = self.client.get(self._url("medicine-import-preview", session_id)).data["records"][0]["mapped_data"]
        self.assertEqual((data["strength"], data["dosage_form"]), ("500mg", "Capsule"))
        self.client.post(self._url("medicine-import-confirm", session_id))
        self.assertEqual(Medicine.objects.get(brand_name="Amoxil 500 mg Caps").description, "Strength: 500mg; Dosage form: Capsule")

    def test_corrected_mapping_is_remembered(self):
        csv_text = "Article,Ref,Nombre,Montant\nPanadol,P1,5,100\n"
        session_id, targets = self._analyze(csv_text)
        mapping = {"Article": "medicine_name", "Ref": "batch_number", "Nombre": "quantity", "Montant": "selling_price"}
        self.client.patch(
            self._url("medicine-import-mapping", session_id),
            {"mapping": [{"source_column": c, "target_field": t} for c, t in mapping.items()]},
            format="json",
        )
        self.assertEqual(self.client.post(self._url("medicine-import-confirm", session_id)).status_code, 200)

        _, targets = self._analyze("Article,Ref,Nombre,Montant\nCoartem,C1,2,300\n")
        self.assertEqual(targets, mapping)

    def test_similar_existing_name_warns(self):
        Medicine.objects.create(tenant=self.tenant, brand_name="Paracetamol 500mg")
        session_id, _ = self._analyze("Medicine,Batch,Qty,Selling Price\nParacetamoll 500mg,B1,5,10\nparacetamol 500 MG,B2,5,10\n")
        rows = self.client.get(self._url("medicine-import-preview", session_id)).data["records"]
        self.assertTrue(any("Looks like existing medicine" in w for w in rows[0]["warnings"]), rows[0]["warnings"])
        self.assertEqual(rows[1]["medicine_status"], "existing")


class MedicineImportParsingTests(TestCase):
    def test_parse_date_formats(self):
        self.assertEqual(parse_date("2030-05-04"), date(2030, 5, 4))
        self.assertEqual(parse_date("04/05/2030"), date(2030, 5, 4))
        self.assertEqual(parse_date("12/2030", month_end=True), date(2030, 12, 31))
        self.assertEqual(parse_date("Feb 2028", month_end=True), date(2028, 2, 29))
        self.assertEqual(parse_date("46000"), date(2025, 12, 9))
        self.assertIsNone(parse_date(""))
        with self.assertRaises(ValueError):
            parse_date("soon")

    def test_parse_medicine_name(self):
        self.assertEqual(parse_medicine_name("Amoxicillin 250mg/5ml Susp"), ("250mg/5ml", "Suspension"))
        self.assertEqual(parse_medicine_name("Doliprane 1 g comprimés"), ("1g", "Tablet"))
        self.assertEqual(parse_medicine_name("Betadine 10% Solution"), ("10%", "Solution"))
        self.assertEqual(parse_medicine_name("Vitamin C"), ("", ""))

    def test_parse_decimal(self):
        self.assertEqual(parse_decimal("1,500 RWF"), Decimal("1500"))
        self.assertIsNone(parse_decimal(" "))
        with self.assertRaises(ValueError):
            parse_decimal("1.2.3")
