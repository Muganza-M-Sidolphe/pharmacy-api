from rest_framework import serializers
from .models import User, Tenant
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'name', 'password')
        

class RegisterTenantSerializer(serializers.Serializer):
    tenantName = serializers.CharField()
    tenantEmail = serializers.EmailField()
    tenantPhone = serializers.CharField()
    address = serializers.CharField()
    licenseNumber = serializers.CharField()
    ownerName = serializers.CharField()
    ownerEmail = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    pharmacyId = serializers.UUIDField(required=False)


# serializer for creating a user

class CreateUserSerializer(serializers.Serializer):
    tenantId = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=[
        "CASHIER",
        "STORE_KEEPER",
        "ACCOUNTANT",
        "PHARMACIST"
    ])

#user serializer for listing users

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'role', 'is_active', 'created_at']


# users/serializers.py

class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "email", "role"]


# Tenant/Pharmacy settings serializer

class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ['id', 'name', 'email', 'phone', 'address', 'license_number', 'country', 'currency', 'is_active', 'created_at']


class PharmacySettingsSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(required=False)
    address = serializers.CharField(required=False)
    licenseNumber = serializers.CharField(required=False)
    country = serializers.CharField(required=False)
    currency = serializers.CharField(required=False)


from .models import Notification


class NotificationModelSerializer(serializers.ModelSerializer):
    tenantId = serializers.UUIDField(source="tenant.id", read_only=True)
    recipientId = serializers.UUIDField(source="recipient.id", allow_null=True, required=False)
    isRead = serializers.BooleanField(source="is_read")
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "tenantId", "title", "message", "recipientId", "isRead", "createdAt"]


class NotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    tenantId = serializers.UUIDField(source="tenant.id")
    title = serializers.CharField()
    message = serializers.CharField()
    recipientId = serializers.UUIDField(source="recipient.id", allow_null=True, required=False)
    isRead = serializers.BooleanField(source="is_read", required=False)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    def create(self, validated_data):
        tenant_id = validated_data.pop('tenant')['id']
        recipient = validated_data.pop('recipient', None)
        recipient_obj = None
        if recipient:
            from .models import User
            recipient_obj = User.objects.filter(id=recipient['id']).first()
        from .models import Notification
        return Notification.objects.create(tenant_id=tenant_id, recipient=recipient_obj, **validated_data)

    def to_representation(self, instance):
        return {
            "id": str(instance.id),
            "tenantId": str(instance.tenant.id),
            "title": instance.title,
            "message": instance.message,
            "recipientId": str(instance.recipient.id) if instance.recipient else None,
            "isRead": instance.is_read,
            "createdAt": instance.created_at,
        }


class NotificationMarkSerializer(serializers.Serializer):
    isRead = serializers.BooleanField()


from .models import Medicine, StockBatch


class StockBatchSerializer(serializers.ModelSerializer):
    expiryDate = serializers.DateField(source='expiry_date')
    manufactureDate = serializers.DateField(source='manufacture_date', allow_null=True)

    class Meta:
        model = StockBatch
        fields = ['id', 'batch_number', 'quantity', 'purchase_price', 'selling_price', 'manufactureDate', 'expiryDate', 'supplier_name', 'supplier_phone', 'supplier_address', 'created_at']


class MedicineSerializer(serializers.ModelSerializer):
    tenantId = serializers.UUIDField(source='tenant.id', read_only=True)
    batches = StockBatchSerializer(many=True, read_only=True)

    class Meta:
        model = Medicine
        fields = ['id', 'tenantId', 'brand_name', 'generic_name', 'manufacturer', 'category', 'unit', 'description', 'created_at', 'batches']


class MedicineDataSerializer(serializers.Serializer):
    brandName = serializers.CharField()
    genericName = serializers.CharField(required=False, allow_blank=True)
    manufacturer = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)


class StockDataSerializer(serializers.Serializer):
    batchNumber = serializers.CharField()
    quantity = serializers.IntegerField()
    purchasePrice = serializers.DecimalField(max_digits=12, decimal_places=2)
    sellingPrice = serializers.DecimalField(max_digits=12, decimal_places=2)
    manufactureDate = serializers.DateField(required=False, allow_null=True)
    expiryDate = serializers.DateField(required=False, allow_null=True)
    supplierName = serializers.CharField(required=False, allow_blank=True)
    supplierPhone = serializers.CharField(required=False, allow_blank=True)
    supplierAddress = serializers.CharField(required=False, allow_blank=True)


class AddMedicineWithStockSerializer(serializers.Serializer):
    tenantId = serializers.UUIDField()
    medicine = MedicineDataSerializer()
    stock = StockDataSerializer()

    def create(self, validated_data):
        tenant_id = validated_data.pop('tenantId')
        medicine_data = validated_data.pop('medicine')
        stock_data = validated_data.pop('stock')

        brand_name = medicine_data.get('brandName')
        generic_name = medicine_data.get('genericName')
        manufacturer = medicine_data.get('manufacturer')
        category = medicine_data.get('category')
        unit = medicine_data.get('unit')
        description = medicine_data.get('description')

        medicine = Medicine.objects.create(
            tenant_id=tenant_id,
            brand_name=brand_name,
            generic_name=generic_name,
            manufacturer=manufacturer,
            category=category,
            unit=unit,
            description=description
        )

        StockBatch.objects.create(
            medicine=medicine,
            batch_number=stock_data.get('batchNumber'),
            quantity=stock_data.get('quantity'),
            purchase_price=stock_data.get('purchasePrice'),
            selling_price=stock_data.get('sellingPrice'),
            manufacture_date=stock_data.get('manufactureDate'),
            expiry_date=stock_data.get('expiryDate'),
            supplier_name=stock_data.get('supplierName'),
            supplier_phone=stock_data.get('supplierPhone'),
            supplier_address=stock_data.get('supplierAddress')
        )

        return medicine


from .models import Sale, SaleItem, UserTenant


class SaleItemSerializer(serializers.ModelSerializer):
    medicineId = serializers.UUIDField(source='medicine.id', read_only=True)
    medicineBrandName = serializers.CharField(source='medicine.brand_name', read_only=True)
    medicineGenericName = serializers.CharField(source='medicine.generic_name', read_only=True)
    batchNumber = serializers.CharField(source='batch.batch_number', read_only=True)
    unitPrice = serializers.DecimalField(source='unit_price', max_digits=12, decimal_places=2)

    class Meta:
        model = SaleItem
        fields = ['id', 'medicineId', 'medicineBrandName', 'medicineGenericName', 'batchNumber', 'quantity', 'unitPrice', 'subtotal']


class SaleSerializer(serializers.ModelSerializer):
    tenantId = serializers.UUIDField(source='tenant.id', read_only=True)
    cashierId = serializers.UUIDField(source='cashier.id', read_only=True)
    approvedBy = serializers.UUIDField(source='approved_by.id', read_only=True, allow_null=True)
    approvedByName = serializers.SerializerMethodField()
    approvedByRole = serializers.SerializerMethodField()
    items = SaleItemSerializer(many=True, read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    approvedAt = serializers.DateTimeField(source='approved_at', read_only=True, allow_null=True)
    paymentOption = serializers.CharField(source='payment_option')
    paymentMethod = serializers.CharField(source='payment_method')
    invoiceNumber = serializers.CharField(source='invoice_number')
    customerName = serializers.CharField(source='customer_name', allow_null=True)
    customerPhone = serializers.CharField(source='customer_phone', allow_null=True)
    discountAmount = serializers.DecimalField(source='discount_amount', max_digits=12, decimal_places=2)
    paidAmount = serializers.DecimalField(source='paid_amount', max_digits=12, decimal_places=2)
    dueAmount = serializers.DecimalField(source='due_amount', max_digits=12, decimal_places=2)
    totalAmount = serializers.DecimalField(source='total_amount', max_digits=12, decimal_places=2)
    status = serializers.SerializerMethodField()

    def get_approvedByName(self, obj):
        if not obj.approved_by:
            return None
        return obj.approved_by.name

    def get_approvedByRole(self, obj):
        if not obj.approved_by:
            return None
        if obj.approved_by.is_super_admin:
            return "SUPER_ADMIN"
        relation = UserTenant.objects.filter(
            user=obj.approved_by,
            tenant=obj.tenant
        ).first()
        return relation.role if relation else None

    def get_status(self, obj):
        # Response label for payment progress.
        if obj.status in ['APPROVED', 'COMPLETED'] and obj.due_amount <= 0:
            return 'PAID'
        if obj.status == 'APPROVED' and obj.due_amount > 0:
            return 'APPROVED (Pending Payment)'
        return obj.status

    class Meta:
        model = Sale
        fields = ['id', 'tenantId', 'cashierId', 'invoiceNumber', 'customerName', 'customerPhone', 'notes', 'status', 
                  'paymentOption', 'paymentMethod', 'subtotal', 'discountAmount', 'paidAmount', 'dueAmount', 
                  'totalAmount', 'items', 'createdAt', 'updatedAt', 'approvedAt', 'approvedBy', 'approvedByName', 'approvedByRole']
        read_only_fields = ['id', 'invoiceNumber', 'status', 'createdAt', 'updatedAt', 'approvedAt', 'approvedBy']


class CreateSaleSerializer(serializers.Serializer):
    """Serializer for creating a new sale."""
    tenantId = serializers.UUIDField()
    customerName = serializers.CharField(required=False, allow_blank=True)
    customerPhone = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    paymentOption = serializers.ChoiceField(choices=['FULL', 'PARTIAL', 'CREDIT'])
    paymentMethod = serializers.ChoiceField(choices=['CASH', 'CARD', 'UPI', 'MOBILE_MONEY', 'BANK_TRANSFER'])
    discountAmount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0)
    paidAmount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # items list: [{"medicineId": uuid, "batchId": uuid, "quantity": int}]
    items = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField()
        )
    )

    def create(self, validated_data):
        from django.utils import timezone
        from django.db.models import F
        
        tenant_id = validated_data['tenantId']
        cashier = self.context['request'].user
        items_data = validated_data['items']
        
        # Generate invoice number
        from datetime import datetime
        invoice_num = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Calculate totals
        subtotal = 0
        discount_amount = validated_data.get('discountAmount', 0)
        paid_amount = validated_data.get('paidAmount', 0)
        
        # Create sale
        sale = Sale.objects.create(
            tenant_id=tenant_id,
            cashier=cashier,
            invoice_number=invoice_num,
            customer_name=validated_data.get('customerName'),
            customer_phone=validated_data.get('customerPhone'),
            notes=validated_data.get('notes'),
            payment_option=validated_data['paymentOption'],
            payment_method=validated_data['paymentMethod'],
            discount_amount=discount_amount,
            paid_amount=paid_amount,
            status='PENDING'
        )
        
        # Create sale items
        for item in items_data:
            from uuid import UUID
            
            # Get IDs with fallback to both naming conventions
            medicine_id_str = item.get('medicineId') or item.get('medicine_id')
            batch_identifier = item.get('batchId') or item.get('batch_id')
            quantity_str = item.get('quantity')
            
            if not medicine_id_str or not batch_identifier or not quantity_str:
                raise serializers.ValidationError({
                    'items': 'Each item must have medicineId, batchId, and quantity'
                })
            
            try:
                medicine_id = UUID(str(medicine_id_str))
                quantity = int(quantity_str)
            except (ValueError, AttributeError) as e:
                raise serializers.ValidationError({
                    'items': f'Invalid medicineId or quantity format: {str(e)}'
                })
            
            # Try to get batch by UUID first, if that fails, try by batch_number
            batch = None
            try:
                batch_id = UUID(str(batch_identifier))
                batch = StockBatch.objects.get(id=batch_id, medicine_id=medicine_id)
            except (ValueError, StockBatch.DoesNotExist):
                # If UUID fails, try to find by batch_number
                batch = StockBatch.objects.filter(
                    batch_number=batch_identifier,
                    medicine_id=medicine_id
                ).first()
            
            if not batch:
                raise serializers.ValidationError({
                    'items': f'Batch {batch_identifier} not found for medicine {medicine_id}'
                })
            medicine = batch.medicine
            unit_price = batch.selling_price
            item_subtotal = unit_price * quantity
            
            SaleItem.objects.create(
                sale=sale,
                medicine=medicine,
                batch=batch,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=item_subtotal
            )
            
            subtotal += item_subtotal
        
        # Update sale totals
        total_amount = subtotal - discount_amount
        due_amount = total_amount - paid_amount
        
        sale.subtotal = subtotal
        sale.total_amount = total_amount
        sale.due_amount = due_amount
        sale.save()
        
        return sale


# --- Accountant sales / dashboard serializers ---
class SalesSummarySerializer(serializers.Serializer):
    totalSales = serializers.IntegerField()
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    averageOrderValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    uniqueCustomers = serializers.IntegerField()


class DailySalesTrendSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    data = serializers.ListField(child=serializers.FloatField())


class PaymentMethodDistributionItemSerializer(serializers.Serializer):
    method = serializers.CharField()
    count = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentage = serializers.FloatField()


class PaymentMethodsDistributionSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    distribution = PaymentMethodDistributionItemSerializer(many=True)


# Expenses serializers
class ExpenseCategorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    class Meta:
        from .models import ExpenseCategory
        model = ExpenseCategory
        fields = ['id', 'name']


class ExpenseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    tenantId = serializers.UUIDField(source='tenant.id', read_only=True)
    category = serializers.CharField(allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(allow_blank=True, allow_null=True)
    expenseDate = serializers.DateField(source='expense_date')
    createdBy = serializers.UUIDField(source='created_by.id', read_only=True, allow_null=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)


class CreateExpenseSerializer(serializers.Serializer):
    tenantId = serializers.UUIDField()
    categoryId = serializers.UUIDField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)
    expenseDate = serializers.DateField()

    def create(self, validated_data):
        from api.models import Expense, ExpenseCategory
        tenant_id = validated_data['tenantId']
        category_id = validated_data.get('categoryId')
        category_obj = None
        if category_id:
            from uuid import UUID
            try:
                category_obj = ExpenseCategory.objects.filter(id=UUID(str(category_id)), tenant_id=tenant_id).first()
            except Exception:
                category_obj = None

        expense = Expense.objects.create(
            tenant_id=tenant_id,
            category=category_obj,
            amount=validated_data['amount'],
            description=validated_data.get('description', ''),
            expense_date=validated_data['expenseDate'],
            created_by=self.context['request'].user
        )
        return expense


class ExpenseSummarySerializer(serializers.Serializer):
    netProfit = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalExpenses = serializers.DecimalField(max_digits=14, decimal_places=2)
    expenseRatio = serializers.FloatField()


class FinancialBreakdownItemSerializer(serializers.Serializer):
    metric = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentageOfRevenue = serializers.FloatField()


class FinancialReportSerializer(serializers.Serializer):
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    netProfit = serializers.DecimalField(max_digits=14, decimal_places=2)
    profitMargin = serializers.FloatField()
    transactions = serializers.IntegerField()
    breakdown = FinancialBreakdownItemSerializer(many=True)


class InventoryReportItemSerializer(serializers.Serializer):
    medicineId = serializers.UUIDField()
    brandName = serializers.CharField()
    totalBatches = serializers.IntegerField()
    totalQuantity = serializers.IntegerField()
    totalValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    expiringBatches = serializers.IntegerField()


class InventoryReportSerializer(serializers.Serializer):
    totalMedicines = serializers.IntegerField()
    totalInventoryValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalUnits = serializers.IntegerField()
    expiringCount = serializers.IntegerField()
    items = InventoryReportItemSerializer(many=True)


class SalesReportItemSerializer(serializers.Serializer):
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField()
    status = serializers.CharField()
    createdAt = serializers.DateTimeField()


class SalesReportSerializer(serializers.Serializer):
    totalSales = serializers.IntegerField()
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalDiscount = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalPaid = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    items = SalesReportItemSerializer(many=True)


# History serializers
class AccountantDashboardSummarySerializer(serializers.Serializer):
    totalSales = serializers.IntegerField()
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalPaid = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalExpenses = serializers.DecimalField(max_digits=14, decimal_places=2)
    expenseCount = serializers.IntegerField()
    notificationsCount = serializers.IntegerField()
    unreadNotificationsCount = serializers.IntegerField()


class NotificationItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    message = serializers.CharField()
    isRead = serializers.BooleanField()
    createdAt = serializers.DateTimeField()


class ApprovedSaleDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField()
    paymentOption = serializers.CharField()
    status = serializers.CharField()
    createdAt = serializers.DateTimeField()


class ApprovedSalesListSerializer(serializers.Serializer):
    results = ApprovedSaleDetailSerializer(many=True)
    pagination = serializers.DictField()

# Analytics serializers
class AnalyticsKPISerializer(serializers.Serializer):
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    avgOrderValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    uniqueCustomers = serializers.IntegerField()
    inventoryTurnover = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentChange = serializers.DecimalField(max_digits=5, decimal_places=2)


class DailyRevenueTrendItemSerializer(serializers.Serializer):
    date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    transactions = serializers.IntegerField()


class DailyRevenueTrendSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    datasets = serializers.ListField(child=serializers.DictField())
    items = DailyRevenueTrendItemSerializer(many=True)


class HourlySalesPatternItemSerializer(serializers.Serializer):
    hour = serializers.TimeField()
    sales = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)


class HourlySalesPatternSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    datasets = serializers.ListField(child=serializers.DictField())
    items = HourlySalesPatternItemSerializer(many=True)


class RevenueVsTransactionsItemSerializer(serializers.Serializer):
    date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    transactions = serializers.IntegerField()


class RevenueVsTransactionsSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    datasets = serializers.ListField(child=serializers.DictField())
    items = RevenueVsTransactionsItemSerializer(many=True)


class TrendsAnalysisSerializer(serializers.Serializer):
    dailyRevenueTrend = DailyRevenueTrendSerializer()
    hourlySalesPattern = HourlySalesPatternSerializer()
    revenueVsTransactions = RevenueVsTransactionsSerializer()


class ForecastItemSerializer(serializers.Serializer):
    date = serializers.DateField()
    forecastedRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    confidence = serializers.DecimalField(max_digits=5, decimal_places=2)


class ForecastsSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    datasets = serializers.ListField(child=serializers.DictField())
    items = ForecastItemSerializer(many=True)


class BusinessInsightSerializer(serializers.Serializer):
    title = serializers.CharField()
    description = serializers.CharField()
    metric = serializers.CharField()
    trend = serializers.CharField()
    recommendation = serializers.CharField()


class BusinessInsightsSerializer(serializers.Serializer):
    insights = BusinessInsightSerializer(many=True)


class AnalyticsDashboardSerializer(serializers.Serializer):
    kpis = AnalyticsKPISerializer()
    trendsAnalysis = TrendsAnalysisSerializer()
    forecasts = ForecastsSerializer()
    businessInsights = BusinessInsightsSerializer()


# Accountant Dashboard serializers
class PendingPartialPaymentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    createdAt = serializers.DateTimeField()


class PendingPartialPaymentsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    totalDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    items = PendingPartialPaymentSerializer(many=True)


class OverduePaymentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    daysOverdue = serializers.IntegerField()
    createdAt = serializers.DateTimeField()


class OverduePaymentsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    items = OverduePaymentSerializer(many=True)


class TotalPaidAmountSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalApprovedSales = serializers.IntegerField()
    period = serializers.CharField()


class SelectedForInvoiceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    createdAt = serializers.DateTimeField()


class SelectedForInvoiceListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    items = SelectedForInvoiceSerializer(many=True)


class PaymentRequestItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    requestedAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    status = serializers.CharField()
    createdAt = serializers.DateTimeField()


class PartialPaymentRequestsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    pending = serializers.IntegerField()
    items = PaymentRequestItemSerializer(many=True)


class QuickStatsSerializer(serializers.Serializer):
    todaysSales = serializers.IntegerField()
    pendingInvoices = serializers.IntegerField()
    pendingPartialPayments = serializers.IntegerField()
    overduePayments = serializers.IntegerField()
    totalDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalPaid = serializers.DecimalField(max_digits=14, decimal_places=2)


class AccountantDashboardPaymentsSerializer(serializers.Serializer):
    pendingPartialPayments = PendingPartialPaymentsSerializer()
    overduePayments = OverduePaymentsSerializer()
    totalPaidAmount = TotalPaidAmountSerializer()
    selectedForInvoice = SelectedForInvoiceListSerializer()
    partialPaymentRequests = PartialPaymentRequestsSerializer()
    quickStats = QuickStatsSerializer()


# Pharmacist Invoice serializers
class PharmacistInvoiceItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    medicineName = serializers.CharField()
    medicineCode = serializers.CharField()
    quantity = serializers.IntegerField()
    unitPrice = serializers.DecimalField(max_digits=12, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)


class PharmacistInvoiceDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    discountAmount = serializers.DecimalField(max_digits=12, decimal_places=2)
    paymentMethod = serializers.CharField()
    paymentOption = serializers.CharField()
    status = serializers.CharField()
    approvalStatus = serializers.CharField()
    approvedBy = serializers.UUIDField(allow_null=True)
    approvedByName = serializers.CharField(allow_null=True)
    approvedByRole = serializers.CharField(allow_null=True)
    items = PharmacistInvoiceItemSerializer(many=True)
    createdAt = serializers.DateTimeField()
    approvedAt = serializers.DateTimeField(allow_null=True)


class PharmacistInvoiceListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField()
    paymentOption = serializers.CharField()
    status = serializers.CharField()
    approvalStatus = serializers.CharField()
    approvedBy = serializers.UUIDField(allow_null=True)
    approvedByName = serializers.CharField(allow_null=True)
    approvedByRole = serializers.CharField(allow_null=True)
    itemCount = serializers.IntegerField()
    createdAt = serializers.DateTimeField()


class PharmacistInvoicesListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True, required=False)
    previous = serializers.CharField(allow_null=True, required=False)
    results = PharmacistInvoiceListItemSerializer(many=True)


class PharmacistInvoicesSummarySerializer(serializers.Serializer):
    totalInvoices = serializers.IntegerField()
    invoicesToday = serializers.IntegerField()
    totalRevenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalCollected = serializers.DecimalField(max_digits=14, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    unpaidInvoices = serializers.IntegerField()
    pendingApproval = serializers.IntegerField()
    approvedInvoices = serializers.IntegerField()


class PharmacistApproveInvoiceSerializer(serializers.Serializer):
    invoiceId = serializers.UUIDField()
    notes = serializers.CharField(required=False, allow_blank=True)


class PharmacistPaymentApprovalItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    requestedAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField()
    status = serializers.CharField()
    requestedAt = serializers.DateTimeField()
    createdAt = serializers.DateTimeField()


class PharmacistPaymentApprovalsListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    pending = serializers.IntegerField()
    results = PharmacistPaymentApprovalItemSerializer(many=True)


class PharmacistApprovePaymentSerializer(serializers.Serializer):
    paymentId = serializers.UUIDField()
    approvalStatus = serializers.CharField()
    notes = serializers.CharField(required=False, allow_blank=True)


class PharmacistInvoiceQuickStatsSerializer(serializers.Serializer):
    lowStock = serializers.IntegerField()
    expiringSoon = serializers.IntegerField()
    pendingApprovals = serializers.IntegerField()


    businessInsights = BusinessInsightsSerializer()


# Partial Payment Approval Serializers

class PartialPaymentInvoiceSerializer(serializers.Serializer):
    invoiceId = serializers.CharField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField()
    paymentOption = serializers.CharField()
    invoiceDate = serializers.DateTimeField()
    dueDate = serializers.DateField(allow_null=True)
    createdAt = serializers.DateTimeField()


class PharmacistPartialPaymentListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = PartialPaymentInvoiceSerializer(many=True)


class PharmacistPartialPaymentSummarySerializer(serializers.Serializer):
    partialPayments = serializers.IntegerField()
    totalPartialDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    overduePayments = serializers.IntegerField()
    totalPaidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    selectedForProcessing = serializers.IntegerField()
    selectedForProcessingTotal = serializers.DecimalField(max_digits=14, decimal_places=2)


class PartialPaymentApprovalRequestSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)


class PartialPaymentRejectRequestSerializer(serializers.Serializer):
    rejectionReason = serializers.CharField()


class OwnerPartialPaymentListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = PartialPaymentInvoiceSerializer(many=True)


class OwnerPartialPaymentSummarySerializer(serializers.Serializer):
    pendingApprovals = serializers.IntegerField()
    totalPendingDue = serializers.DecimalField(max_digits=14, decimal_places=2)
    approvedByPharmacist = serializers.IntegerField()
    totalApprovedAmount = serializers.DecimalField(max_digits=14, decimal_places=2)

# Pharmacist History Serializers

class PharmacistApprovalHistoryItemSerializer(serializers.Serializer):
    invoiceId = serializers.CharField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    customerPhone = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paidAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    dueAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paymentMethod = serializers.CharField(allow_null=True)
    paymentOption = serializers.CharField()
    status = serializers.CharField()
    approvalStatus = serializers.CharField()
    invoiceDate = serializers.DateTimeField()
    approvedAt = serializers.DateTimeField(allow_null=True)
    approvedBy = serializers.CharField(allow_null=True)
    notes = serializers.CharField(allow_null=True)


class PharmacistApprovalHistoryListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = PharmacistApprovalHistoryItemSerializer(many=True)


class PharmacistHistorySummarySerializer(serializers.Serializer):
    totalApprovals = serializers.IntegerField()
    pendingApproval = serializers.IntegerField()
    partialPayments = serializers.IntegerField()
    totalValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalApprovedValue = serializers.DecimalField(max_digits=14, decimal_places=2)
    totalRejected = serializers.IntegerField()


class PharmacistHistoryExportSerializer(serializers.Serializer):
    startDate = serializers.DateField(required=False)
    endDate = serializers.DateField(required=False)
    status = serializers.CharField(required=False)
    paymentMethod = serializers.CharField(required=False)



# Pharmacist Dashboard Serializers

class PharmacistDashboardPendingItemSerializer(serializers.Serializer):
    invoiceId = serializers.CharField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    invoiceDate = serializers.DateTimeField()
    status = serializers.CharField()


class PharmacistDashboardRecentApprovalSerializer(serializers.Serializer):
    invoiceId = serializers.CharField()
    invoiceNumber = serializers.CharField()
    customerName = serializers.CharField(allow_null=True)
    totalAmount = serializers.DecimalField(max_digits=14, decimal_places=2)
    approvedAt = serializers.DateTimeField()
    approvalStatus = serializers.CharField()


class PharmacistDashboardSummarySerializer(serializers.Serializer):
    pendingReview = serializers.IntegerField()
    approvedToday = serializers.IntegerField()
    partialPayments = serializers.IntegerField()
    totalProcessed = serializers.DecimalField(max_digits=14, decimal_places=2)
    lowStock = serializers.IntegerField()
    expiringSoon = serializers.IntegerField()
    pendingApprovals = serializers.IntegerField()


class PharmacistDashboardPendingListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PharmacistDashboardPendingItemSerializer(many=True)


class PharmacistDashboardRecentApprovalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PharmacistDashboardRecentApprovalSerializer(many=True)
