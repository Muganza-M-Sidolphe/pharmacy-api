# Accountant payments views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from django.http import HttpResponse
import csv

from ...models import UserTenant, Sale
from ...serializers import SaleSerializer, SalesSummarySerializer, DailySalesTrendSerializer, PaymentMethodsDistributionSerializer
from drf_spectacular.utils import extend_schema


class AccountantSalesSummaryView(APIView):
	"""Summary metrics for accountant sales dashboard."""
	permission_classes = [IsAuthenticated]

	@extend_schema(
		description="Get sales summary: total sales, revenue, average order, unique customers",
		responses=SalesSummarySerializer,
		tags=["accountant"]
	)
	def get(self, request):
		tenant_id = request.query_params.get('tenantId')
		if not tenant_id:
			return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

		if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
			return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

		qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])

		total_sales = qs.count()
		total_revenue = sum((s.total_amount for s in qs), Decimal('0.00'))
		average_order = (total_revenue / total_sales) if total_sales else Decimal('0.00')

		# Unique customers based on phone or name
		unique_keys = set()
		for s in qs:
			key = s.customer_phone or s.customer_name
			if key:
				unique_keys.add(key)

		unique_customers = len(unique_keys)

		return Response({
			'totalSales': total_sales,
			'totalRevenue': str(total_revenue),
			'averageOrderValue': str(average_order.quantize(Decimal('0.01'))),
			'uniqueCustomers': unique_customers
		})


class AccountantDailySalesTrendView(APIView):
	"""Daily sales totals for a date range (defaults to last 7 days)."""
	permission_classes = [IsAuthenticated]

	@extend_schema(
		description="Get daily sales totals for charting",
		responses=DailySalesTrendSerializer,
		tags=["accountant"]
	)
	def get(self, request):
		tenant_id = request.query_params.get('tenantId')
		if not tenant_id:
			return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

		if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
			return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

		start_date = request.query_params.get('startDate')
		end_date = request.query_params.get('endDate')

		try:
			if start_date:
				start = datetime.strptime(start_date, '%Y-%m-%d').date()
			else:
				start = (timezone.now() - timedelta(days=6)).date()

			if end_date:
				end = datetime.strptime(end_date, '%Y-%m-%d').date()
			else:
				end = timezone.now().date()
		except ValueError:
			return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

		# Build date range
		delta = (end - start).days
		labels = []
		data = []

		for i in range(delta + 1):
			day = start + timedelta(days=i)
			day_total = Sale.objects.filter(
				tenant_id=tenant_id,
				status__in=['APPROVED', 'COMPLETED'],
				created_at__date=day
			)
			total_amount = sum((s.total_amount for s in day_total), Decimal('0.00'))
			labels.append(day.strftime('%a'))
			data.append(float(total_amount))

		return Response({'labels': labels, 'data': data})


class AccountantPaymentMethodsDistributionView(APIView):
	"""Distribution of payment methods for a tenant and date range."""
	permission_classes = [IsAuthenticated]

	@extend_schema(
		description="Get payment methods distribution",
		responses=PaymentMethodsDistributionSerializer,
		tags=["accountant"]
	)
	def get(self, request):
		tenant_id = request.query_params.get('tenantId')
		if not tenant_id:
			return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

		if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
			return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

		start_date = request.query_params.get('startDate')
		end_date = request.query_params.get('endDate')

		qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])

		if start_date:
			try:
				start = datetime.strptime(start_date, '%Y-%m-%d').date()
				qs = qs.filter(created_at__date__gte=start)
			except ValueError:
				pass
		if end_date:
			try:
				end = datetime.strptime(end_date, '%Y-%m-%d').date()
				qs = qs.filter(created_at__date__lte=end)
			except ValueError:
				pass

		total_count = qs.count()
		by_method = {}
		for s in qs:
			method = s.payment_method or 'UNKNOWN'
			if method not in by_method:
				by_method[method] = {'count': 0, 'amount': Decimal('0.00')}
			by_method[method]['count'] += 1
			by_method[method]['amount'] += s.paid_amount

		result = []
		for method, data in by_method.items():
			pct = (data['count'] / total_count * 100) if total_count else 0
			result.append({
				'method': method,
				'count': data['count'],
				'amount': str(data['amount']),
				'percentage': round(pct, 1)
			})

		return Response({'total': total_count, 'distribution': result})


class AccountantExportSalesView(APIView):
	"""Export sales as CSV for a date range."""
	permission_classes = [IsAuthenticated]

	@extend_schema(
		description="Export sales CSV",
		tags=["accountant"],
		responses=None
	)
	def get(self, request):
		tenant_id = request.query_params.get('tenantId')
		if not tenant_id:
			return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

		if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
			return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

		start_date = request.query_params.get('startDate')
		end_date = request.query_params.get('endDate')

		qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED']).order_by('created_at')

		if start_date:
			try:
				start = datetime.strptime(start_date, '%Y-%m-%d').date()
				qs = qs.filter(created_at__date__gte=start)
			except ValueError:
				pass
		if end_date:
			try:
				end = datetime.strptime(end_date, '%Y-%m-%d').date()
				qs = qs.filter(created_at__date__lte=end)
			except ValueError:
				pass

		# Build CSV
		response = HttpResponse(content_type='text/csv')
		filename = f"sales_{tenant_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
		response['Content-Disposition'] = f'attachment; filename="{filename}"'
		response['Access-Control-Expose-Headers'] = 'Content-Disposition'

		writer = csv.writer(response)
		writer.writerow(['InvoiceNumber', 'CustomerName', 'CustomerPhone', 'TotalAmount', 'PaidAmount', 'DueAmount', 'PaymentMethod', 'PaymentOption', 'Status', 'CreatedAt'])

		for s in qs:
			writer.writerow([
				s.invoice_number,
				s.customer_name or '',
				s.customer_phone or '',
				str(s.total_amount),
				str(s.paid_amount),
				str(s.due_amount),
				s.payment_method,
				s.payment_option,
				s.status,
				s.created_at.isoformat()
			])

		return response
