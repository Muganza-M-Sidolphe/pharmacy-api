from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from ...models import Medicine, MedicineImportRecord, MedicineImportSession, StockBatch
from ...utils import medicine_import as importer
from .stock_batches import StockPermissionView, record_movement, visible_medicines

CAPABILITIES = {"edit_records": True, "remove_records": True}


class MedicineImportBaseView(StockPermissionView):
    required_permission = "medicine.import"
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _get_session(self, request, session_id):
        """Return (tenant, session, error response). Sessions are private to their creator."""
        tenant, error = self._authorize_permission(request)
        if error:
            return None, None, error
        session = MedicineImportSession.objects.filter(
            id=session_id, tenant=tenant, created_by=request.user
        ).first()
        if not session:
            return None, None, Response({"detail": "Import session not found."}, status=status.HTTP_404_NOT_FOUND)
        return tenant, session, None

    def _completed_error(self, session):
        if session.status == "COMPLETED":
            return Response({"detail": "This import has already been completed."}, status=status.HTTP_409_CONFLICT)
        return None

    def _evaluate(self, session, user):
        records = list(session.records.filter(removed=False))
        medicines = {}
        for medicine in visible_medicines(session.tenant_id, user).order_by("created_at"):
            medicines.setdefault(importer.normalize_name(medicine.brand_name), medicine)
        batches = {
            (medicine_id, importer.normalize_name(batch_number))
            for medicine_id, batch_number in StockBatch.objects.filter(
                medicine__in=list(medicines.values())
            ).values_list("medicine_id", "batch_number")
        }
        today = timezone.localdate()
        return records, importer.evaluate_records(session, records, medicines, batches, today=today)


class ImportSessionCreateView(MedicineImportBaseView):
    """Upload a file and read its rows."""

    def post(self, request):
        tenant, error = self._authorize_permission(request)
        if error:
            return error
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Choose a file to upload."}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > importer.MAX_FILE_SIZE:
            return Response({"detail": "The file is larger than 10 MB."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_type, rows = importer.read_rows(upload.name, upload.read())
            columns, records = importer.extract_table(rows)
        except importer.ImportFileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not records:
            return Response({"detail": "The file has a header row but no data rows."}, status=status.HTTP_400_BAD_REQUEST)

        detected = importer.detect_columns(columns)
        with transaction.atomic():
            session = MedicineImportSession.objects.create(
                tenant=tenant,
                created_by=request.user,
                file_name=upload.name[:255],
                file_type=file_type,
                columns=columns,
                mapping={item["name"]: item["target_field"] for item in detected},
            )
            MedicineImportRecord.objects.bulk_create([
                MedicineImportRecord(session=session, row_number=row_number, raw=values)
                for row_number, values in records
            ])

        return Response({
            "session_id": str(session.id),
            "id": str(session.id),
            "file_name": session.file_name,
            "file_type": file_type,
            "rows_count": len(records),
        }, status=status.HTTP_201_CREATED)


class ImportSessionAnalyzeView(MedicineImportBaseView):
    """Detected columns with a suggested field and confidence for each."""

    def post(self, request, session_id):
        _, session, error = self._get_session(request, session_id)
        if error:
            return error
        detected = importer.detect_columns(session.columns)
        sample = [record.raw for record in session.records.all()[:5]]
        return Response({
            "session_id": str(session.id),
            "detected_columns": detected,
            "rows_count": session.records.count(),
            "sample_rows": sample,
            "capabilities": CAPABILITIES,
        })


class ImportSessionMappingView(MedicineImportBaseView):
    def patch(self, request, session_id):
        _, session, error = self._get_session(request, session_id)
        if error:
            return error
        completed = self._completed_error(session)
        if completed:
            return completed

        valid_targets = set(importer.TARGET_FIELDS) | {importer.IGNORE}
        mapping = {}
        for item in request.data.get("mapping") or []:
            column = item.get("source_column")
            target = item.get("target_field") or importer.IGNORE
            if column not in session.columns:
                continue
            if target not in valid_targets:
                return Response({"detail": f"Unknown field '{target}'."}, status=status.HTTP_400_BAD_REQUEST)
            mapping[column] = target
        if "medicine_name" not in mapping.values():
            return Response({"detail": "Map one column to Medicine Name."}, status=status.HTTP_400_BAD_REQUEST)

        pricing = request.data.get("pricing") or {}
        method = pricing.get("method") or "file"
        if method not in {"file", "markup", "manual"}:
            return Response({"detail": "pricing.method must be file, markup or manual."}, status=status.HTTP_400_BAD_REQUEST)
        markup = None
        if method == "markup":
            try:
                markup = Decimal(str(pricing.get("markup_percent")))
            except (InvalidOperation, TypeError, ValueError):
                markup = None
            if markup is None or not markup.is_finite() or markup < 0 or markup > 10000:
                return Response({"detail": "Markup must be a number of 0 or more."}, status=status.HTTP_400_BAD_REQUEST)

        strategy = request.data.get("missing_batch_number_strategy") or "manual"
        if strategy not in {"manual", "generate"}:
            return Response(
                {"detail": "missing_batch_number_strategy must be manual or generate."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session.mapping = mapping
        session.pricing_method = method
        session.markup_percent = markup
        session.missing_batch_strategy = strategy
        session.save(update_fields=["mapping", "pricing_method", "markup_percent", "missing_batch_strategy", "updated_at"])
        return Response({
            "session_id": str(session.id),
            "mapping": [{"source_column": c, "target_field": t} for c, t in mapping.items()],
            "pricing": {"method": method, "markup_percent": float(markup) if markup is not None else None},
            "missing_batch_number_strategy": strategy,
            "capabilities": CAPABILITIES,
        })


class ImportSessionPreviewView(MedicineImportBaseView):
    def get(self, request, session_id):
        _, session, error = self._get_session(request, session_id)
        if error:
            return error
        _, results = self._evaluate(session, request.user)
        return Response({
            "session_id": str(session.id),
            "status": session.status,
            "records": [importer.public_record(result) for result in results],
            "summary": importer.summarize(results),
            "capabilities": CAPABILITIES,
        })


class ImportRecordView(MedicineImportBaseView):
    """Correct a field on one row, or remove the row from the import."""

    def _get_record(self, request, session_id, record_id):
        _, session, error = self._get_session(request, session_id)
        if error:
            return None, error
        completed = self._completed_error(session)
        if completed:
            return None, completed
        record = session.records.filter(id=record_id, removed=False).first()
        if not record:
            return None, Response({"detail": "Import row not found."}, status=status.HTTP_404_NOT_FOUND)
        return record, None

    def patch(self, request, session_id, record_id):
        record, error = self._get_record(request, session_id, record_id)
        if error:
            return error
        changes = {
            field: ("" if value is None else str(value).strip())
            for field, value in request.data.items()
            if field in importer.TARGET_FIELDS
        }
        if not changes:
            return Response({"detail": "No editable fields were sent."}, status=status.HTTP_400_BAD_REQUEST)
        record.overrides = {**(record.overrides or {}), **changes}
        record.save(update_fields=["overrides"])
        return Response({"id": str(record.id), "row_number": record.row_number, "overrides": record.overrides})

    def delete(self, request, session_id, record_id):
        record, error = self._get_record(request, session_id, record_id)
        if error:
            return error
        record.removed = True
        record.save(update_fields=["removed"])
        return Response({"id": str(record.id), "removed": True})


class ImportSessionConfirmView(MedicineImportBaseView):
    """Create medicines and batches for every valid row. Invalid rows are skipped."""

    def post(self, request, session_id):
        tenant, session, error = self._get_session(request, session_id)
        if error:
            return error

        with transaction.atomic():
            session = MedicineImportSession.objects.select_for_update().get(id=session.id)
            completed = self._completed_error(session)
            if completed:
                return completed
            _, results = self._evaluate(session, request.user)
            valid = [result for result in results if result["is_valid"]]
            if not valid:
                return Response({"detail": "There are no valid rows to import."}, status=status.HTTP_400_BAD_REQUEST)

            created_medicines = {}
            updated_medicine_ids = set()
            for result in valid:
                values, parsed = result["mapped_data"], result["_parsed"]
                medicine = None
                if result["existing_medicine_id"]:
                    medicine = Medicine.objects.get(id=result["existing_medicine_id"])
                    updated_medicine_ids.add(medicine.id)
                else:
                    key = importer.normalize_name(values["medicine_name"])
                    medicine = created_medicines.get(key)
                    if medicine is None:
                        medicine = Medicine.objects.create(
                            tenant=tenant,
                            created_by=request.user,
                            brand_name=values["medicine_name"][:255],
                            generic_name=values["generic_name"][:255] or None,
                            manufacturer=values["manufacturer"][:255] or None,
                            category=values["category"][:255] or None,
                            unit=values["unit"][:50] or None,
                            description=_description(values),
                        )
                        created_medicines[key] = medicine

                batch = StockBatch.objects.create(
                    medicine=medicine,
                    created_by=request.user,
                    batch_number=values["batch_number"][:100],
                    quantity=parsed["quantity"],
                    purchase_price=parsed.get("cost_price", Decimal("0")),
                    selling_price=parsed["selling_price"],
                    manufacture_date=parsed.get("manufacturing_date"),
                    expiry_date=parsed.get("expiry_date"),
                    supplier_name=values["supplier"][:255] or None,
                )
                record_movement(
                    batch, tenant, request.user, "IMPORT", batch.quantity,
                    reference=f"{session.file_name} row {result['row_number']}"[:255],
                )

            outcome = {
                "records_processed": len(valid),
                "new_medicines": len(created_medicines),
                "updated_medicines": len(updated_medicine_ids),
                "batches_created": len(valid),
                "skipped": len(results) - len(valid),
            }
            session.status = "COMPLETED"
            session.result = outcome
            session.completed_at = timezone.now()
            session.save(update_fields=["status", "result", "completed_at", "updated_at"])

        return Response({"session_id": str(session.id), **outcome})


def _description(values):
    """Medicine has no fields for strength, dosage form or barcode, so keep them in the description."""
    parts = [
        f"{label}: {values[field]}"
        for field, label in (("strength", "Strength"), ("dosage_form", "Dosage form"), ("barcode", "Barcode"))
        if values[field]
    ]
    return "; ".join(parts) or None
