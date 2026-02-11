from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta

from ...models import StockBatch, UserTenant
from ...serializers import StockBatchSerializer
from drf_spectacular.utils import extend_schema


class ExpiryAlertsView(APIView):
    """API endpoints for expiry alerts module."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get medicine batches expiring soon with pagination and search",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List expiring batches (default: within 30 days)."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        days = int(request.query_params.get('days', 30))
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        query = request.query_params.get('query', '').strip()

        today = timezone.now().date()
        cutoff = today + timedelta(days=days)

        qs = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
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


class ExpiryAlertsSummaryView(APIView):
    """Get expiry alerts summary counts."""
    permission_classes = [IsAuthenticated]

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
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()
        
        # Total expiring within 30 days
        cutoff_30 = today + timedelta(days=30)
        total_expiring = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__isnull=False,
            expiry_date__lte=cutoff_30,
            expiry_date__gt=today
        ).count()

        # Critical: expiring within 7 days
        cutoff_7 = today + timedelta(days=7)
        critical = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__isnull=False,
            expiry_date__lte=cutoff_7,
            expiry_date__gt=today
        ).count()

        # Expired: already past expiry date
        expired = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__isnull=False,
            expiry_date__lt=today
        ).count()

        return Response({
            'totalExpiring': total_expiring,
            'critical': critical,
            'expired': expired
        })


class ExpiryAlertsCriticalView(APIView):
    """Get critical expiry alerts (expiring within 7 days)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get batches expiring within 7 days (critical alerts)",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List batches expiring within 7 days."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        today = timezone.now().date()
        cutoff_7 = today + timedelta(days=7)

        qs = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
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


class ExpiredBatchesView(APIView):
    """Get already expired batches."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get batches that have already expired",
        responses=StockBatchSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List expired batches."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        today = timezone.now().date()

        qs = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
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
