from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from ...models import UserTenant, StockBatch
from ...serializers import StockBatchSerializer
from drf_spectacular.utils import extend_schema


class CashierDashboardSummaryView(APIView):
    """API for cashier dashboard overview metrics."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get cashier dashboard summary: today's sales, available medicines, pending requests, average sale, customers today",
        tags=["cashier"]
    )
    def get(self, request):
        """
        Returns dashboard summary metrics:
        - todaysSales: total sales amount for today
        - transactionsCount: number of transactions today
        - availableMedicines: count of medicines in stock
        - pendingRequests: count of pending approval requests
        - averageSale: average sale per transaction today
        - customersToday: count of unique customers today
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()

        # TODO: Query actual sales data from Invoice/Sale model when available
        # For now, returning placeholder values matching the dashboard
        todays_sales = Decimal('0.00')
        transactions_count = 0
        average_sale = Decimal('0.00')
        customers_today = 0

        # Count available medicines (in stock)
        from ...models import Medicine
        available_medicines = Medicine.objects.filter(
            tenant_id=tenant_id
        ).count()

        # TODO: Query pending requests from Request model when available
        pending_requests = 0

        return Response({
            'todaysSales': str(todays_sales),
            'transactionsCount': transactions_count,
            'availableMedicines': available_medicines,
            'pendingRequests': pending_requests,
            'averageSale': str(average_sale),
            'customersToday': customers_today
        })


class CashierStockAlertsView(APIView):
    """Get stock alerts for cashier (low stock medicines)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get medicines with low stock that need attention",
        tags=["cashier"]
    )
    def get(self, request):
        """
        Returns medicines with stock below threshold.
        Each medicine shows: brand_name, generic_name, current_stock, min_threshold
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        from ...models import Medicine
        
        # Get all medicines with their stock levels
        # TODO: Add min_threshold field to Medicine model if not present
        medicines = Medicine.objects.filter(tenant_id=tenant_id).prefetch_related('batches')

        # Filter for low stock - calculate total quantity per medicine
        low_stock_medicines = []
        for medicine in medicines:
            total_qty = sum(batch.quantity for batch in medicine.batches.all())
            # TODO: Replace hardcoded 10 with medicine.min_threshold once added to model
            if total_qty < 10:  # low stock threshold
                low_stock_medicines.append({
                    'id': str(medicine.id),
                    'brandName': medicine.brand_name,
                    'genericName': medicine.generic_name,
                    'manufacturer': medicine.manufacturer,
                    'currentStock': total_qty,
                    'minThreshold': 10,
                    'status': 'low_stock' if total_qty > 0 else 'out_of_stock'
                })

        paginator = Paginator(low_stock_medicines, page_size)
        page_obj = paginator.get_page(page)

        return Response({
            'results': page_obj.object_list,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            },
            'message': 'All medicines have sufficient stock' if not low_stock_medicines else f'{len(low_stock_medicines)} medicines need attention'
        })


class CashierAvailableMedicinesView(APIView):
    """Get all available medicines for cashier sales."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="List all available medicines with current stock levels",
        tags=["cashier"]
    )
    def get(self, request):
        """List all available medicines with stock levels and pricing."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        query = request.query_params.get('query', '').strip()

        from ...models import Medicine

        qs = Medicine.objects.filter(tenant_id=tenant_id)
        
        if query:
            qs = qs.filter(Q(brand_name__icontains=query) | Q(generic_name__icontains=query))

        qs = qs.prefetch_related('batches').order_by('-created_at')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        medicines_data = []
        for medicine in page_obj:
            batches = StockBatchSerializer(medicine.batches.all(), many=True).data
            total_qty = sum(batch['quantity'] for batch in batches)
            avg_price = Decimal('0.00')
            if batches and total_qty > 0:
                weighted_total = sum(
                    Decimal(b.get('sellingPrice') or b.get('selling_price') or '0') * b.get('quantity', 0)
                    for b in batches
                )
                avg_price = weighted_total / total_qty
            
            medicines_data.append({
                'id': str(medicine.id),
                'brandName': medicine.brand_name,
                'genericName': medicine.generic_name,
                'manufacturer': medicine.manufacturer,
                'category': medicine.category,
                'unit': medicine.unit,
                'description': medicine.description,
                'totalStock': total_qty,
                'averagePrice': str(avg_price),
                'batches': batches
            })

        return Response({
            'results': medicines_data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierPendingRequestsView(APIView):
    """Get pending requests for cashier."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="List pending requests awaiting approval",
        tags=["cashier"]
    )
    def get(self, request):
        """
        Returns list of pending requests (e.g., stock requests, refunds, etc.)
        TODO: Implement once Request model is available
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        # TODO: Query actual requests from Request model when available
        pending_requests = []

        paginator = Paginator(pending_requests, page_size)
        page_obj = paginator.get_page(page)

        return Response({
            'results': page_obj.object_list,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierExpiryAlertsView(APIView):
    """Get expiry alerts for cashier (medicines expiring soon)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get medicines expiring within 30 days",
        tags=["cashier"]
    )
    def get(self, request):
        """List batches expiring soon that cashier should be aware of."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        days = int(request.query_params.get('days', 30))

        today = timezone.now().date()
        cutoff = today + timedelta(days=days)

        batches = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__isnull=False,
            expiry_date__lte=cutoff,
            expiry_date__gt=today
        ).order_by('expiry_date')

        paginator = Paginator(batches, page_size)
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
