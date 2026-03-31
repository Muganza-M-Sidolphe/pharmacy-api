from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from datetime import datetime
from decimal import Decimal

from ...models import Expense, ExpenseCategory, Sale, UserTenant
from ...serializers import ExpenseSerializer, CreateExpenseSerializer, ExpenseSummarySerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class AccountantExpensesBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _authorize(self, request, tenant_id):
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, Response({"detail": error_message}, status=error_status)
        return tenant, None

    def _expense_scope(self, tenant_id):
        qs = Expense.objects.filter(tenant_id=tenant_id)
        is_wholesale_tenant = UserTenant.objects.filter(tenant_id=tenant_id, role="OWNER").exists()
        if is_wholesale_tenant:
            qs = qs.exclude(created_by__department="RETAIL")
        return qs


class AccountantExpensesListCreateView(AccountantExpensesBaseView):
    """List and create expenses for a tenant."""

    @extend_schema(
        description="List expenses with filters or create a new expense",
        tags=["accountant"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        qs = self._expense_scope(tenant_id).order_by('-expense_date')

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(expense_date__gte=start)
            except ValueError:
                pass
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(expense_date__lte=end)
            except ValueError:
                pass

        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category__name__icontains=category)

        search = request.query_params.get('search')
        if search:
            qs = qs.filter(description__icontains=search)

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        data = []
        for e in page_obj:
            data.append({
                'id': str(e.id),
                'tenantId': str(e.tenant.id),
                'category': e.category.name if e.category else None,
                'amount': str(e.amount),
                'description': e.description,
                'expenseDate': e.expense_date,
                'createdBy': str(e.created_by.id) if e.created_by else None,
                'createdAt': e.created_at,
            })

        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })

    @extend_schema(
        description="Create a new expense",
        request=CreateExpenseSerializer,
        responses=ExpenseSerializer,
        tags=["accountant"]
    )
    def post(self, request):
        serializer = CreateExpenseSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # tenant check
        tenant_id = serializer.validated_data['tenantId']
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        expense = serializer.save()
        return Response({
            'id': str(expense.id),
            'tenantId': str(expense.tenant.id),
            'category': expense.category.name if expense.category else None,
            'amount': str(expense.amount),
            'description': expense.description,
            'expenseDate': expense.expense_date,
            'createdBy': str(expense.created_by.id) if expense.created_by else None,
            'createdAt': expense.created_at,
        }, status=status.HTTP_201_CREATED)


class AccountantExpenseDetailView(AccountantExpensesBaseView):

    @extend_schema(
        description="Get expense detail",
        responses=ExpenseSerializer,
        tags=["accountant"]
    )
    def get(self, request, expense_id):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        try:
            e = self._expense_scope(tenant_id).get(id=expense_id)
            return Response({
                'id': str(e.id),
                'tenantId': str(e.tenant.id),
                'category': e.category.name if e.category else None,
                'amount': str(e.amount),
                'description': e.description,
                'expenseDate': e.expense_date,
                'createdBy': str(e.created_by.id) if e.created_by else None,
                'createdAt': e.created_at,
            })
        except Expense.DoesNotExist:
            return Response({"detail": "Expense not found"}, status=status.HTTP_404_NOT_FOUND)

    @extend_schema(
        description="Update expense",
        request=CreateExpenseSerializer,
        responses=ExpenseSerializer,
        tags=["accountant"]
    )
    def put(self, request, expense_id):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        try:
            e = self._expense_scope(tenant_id).get(id=expense_id)
        except Expense.DoesNotExist:
            return Response({"detail": "Expense not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreateExpenseSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        e.amount = data['amount']
        e.description = data.get('description', '')
        e.expense_date = data['expenseDate']
        category_id = data.get('categoryId')
        if category_id:
            try:
                from uuid import UUID
                cat = ExpenseCategory.objects.filter(id=UUID(str(category_id)), tenant_id=tenant_id).first()
                e.category = cat
            except Exception:
                e.category = None
        else:
            e.category = None

        e.save()
        return Response({
            'id': str(e.id),
            'tenantId': str(e.tenant.id),
            'category': e.category.name if e.category else None,
            'amount': str(e.amount),
            'description': e.description,
            'expenseDate': e.expense_date,
            'createdBy': str(e.created_by.id) if e.created_by else None,
            'createdAt': e.created_at,
        })

    @extend_schema(
        description="Delete an expense",
        tags=["accountant"],
        responses={"204": None}
    )
    def delete(self, request, expense_id):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        try:
            e = self._expense_scope(tenant_id).get(id=expense_id)
            e.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Expense.DoesNotExist:
            return Response({"detail": "Expense not found"}, status=status.HTTP_404_NOT_FOUND)


class AccountantExpensesSummaryView(AccountantExpensesBaseView):

    @extend_schema(
        description="Get expenses and revenue summary (net profit/loss)",
        responses=ExpenseSummarySerializer,
        tags=["accountant"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        expenses_qs = self._expense_scope(tenant_id)
        sales_qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])

        if start_date:
            try:
                s = datetime.strptime(start_date, '%Y-%m-%d').date()
                expenses_qs = expenses_qs.filter(expense_date__gte=s)
                sales_qs = sales_qs.filter(created_at__date__gte=s)
            except ValueError:
                pass
        if end_date:
            try:
                e = datetime.strptime(end_date, '%Y-%m-%d').date()
                expenses_qs = expenses_qs.filter(expense_date__lte=e)
                sales_qs = sales_qs.filter(created_at__date__lte=e)
            except ValueError:
                pass

        total_expenses = sum((x.amount for x in expenses_qs), Decimal('0.00'))
        total_revenue = sum((s.total_amount for s in sales_qs), Decimal('0.00'))
        net_profit = total_revenue - total_expenses
        expense_ratio = float((total_expenses / total_revenue * 100) if total_revenue and total_revenue != 0 else 0.0)

        return Response({
            'netProfit': str(net_profit),
            'totalRevenue': str(total_revenue),
            'totalExpenses': str(total_expenses),
            'expenseRatio': round(expense_ratio, 2)
        })
