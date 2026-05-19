from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from ...models import StockBatch, UserTenant
from ...serializers import StockBatchSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class StorekeeperExpiryBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "expiry_alerts"

    def _authorize(self, request):
        tenant_id = request.query_params.get('tenantId')
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, tenant_id, Response({"detail": error_message}, status=error_status)
        return tenant, tenant_id, None

    def _expiry_batches_queryset(self, tenant_id):
        qs = StockBatch.objects.filter(medicine__tenant_id=tenant_id)
        if UserTenant.objects.filter(tenant_id=tenant_id, role="OWNER").exists():
            qs = qs.filter(Q(created_by__department="WHOLESALE") | Q(created_by__isnull=True))
        return qs


class ExpiryAlertsView(StorekeeperExpiryBaseView):
    """API endpoints for expiry alerts module."""

    @extend_schema(
        description="Get medicine batches expiring soon with pagination and search",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List expiring batches (default: within 30 days)."""
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        days = int(request.query_params.get('days', 30))
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        query = request.query_params.get('query', '').strip()

        today = timezone.now().date()
        cutoff = today + timedelta(days=days)

        qs = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lte=cutoff
        ).order_by('expiry_date')

        # Optional search by medicine brand/generic name or batch number
        if query:
            qs = qs.filter(
                medicine__brand_name__icontains=query
            ) | qs.filter(
                medicine__generic_name__icontains=query
            ) | qs.filter(
                batch_number__icontains=query
            )

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        data = StockBatchSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class ExpiryAlertsSummaryView(StorekeeperExpiryBaseView):
    """Get expiry alerts summary counts."""

    @extend_schema(
        description="Get expiry alerts summary: total expiring, critical, and expired counts",
        tags=["storekeeper"]
    )
    def get(self, request):
        """
        Returns:
        - totalExpiring: batches expiring within default 30 days
        - critical: batches expiring within 7 days (urgent)
        - expired: batches already expired
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        today = timezone.now().date()
        
        # Total expiring within 30 days
        cutoff_30 = today + timedelta(days=30)
        total_expiring = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lte=cutoff_30,
            expiry_date__gt=today
        ).count()

        # Critical: expiring within 7 days
        cutoff_7 = today + timedelta(days=7)
        critical = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lte=cutoff_7,
            expiry_date__gt=today
        ).count()

        # Expired: already past expiry date
        expired = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lt=today
        ).count()

        return Response({
            'totalExpiring': total_expiring,
            'critical': critical,
            'expired': expired
        })


class ExpiryAlertsCriticalView(StorekeeperExpiryBaseView):
    """Get critical expiry alerts (expiring within 7 days)."""

    @extend_schema(
        description="Get batches expiring within 7 days (critical alerts)",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List batches expiring within 7 days."""
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        today = timezone.now().date()
        cutoff_7 = today + timedelta(days=7)

        qs = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lte=cutoff_7,
            expiry_date__gt=today
        ).order_by('expiry_date')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        data = StockBatchSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class ExpiredBatchesView(StorekeeperExpiryBaseView):
    """Get already expired batches."""

    @extend_schema(
        description="Get batches that have already expired",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List expired batches."""
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        today = timezone.now().date()

        qs = self._expiry_batches_queryset(tenant_id).filter(
            expiry_date__isnull=False,
            expiry_date__lt=today
        ).order_by('-expiry_date')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        data = StockBatchSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })
