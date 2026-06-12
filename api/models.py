# MOdels for multi-tenant user management system        
import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.contrib.auth.hashers import make_password, check_password

class CUSTOMUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_staff', True)

        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')

        return self.create_user(email, password, **extra_fields)
    
    def get_by_natural_key(self, email):
        return self.get(email=email)

#Tenant model

class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50)
    address = models.TextField()
    license_number = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default="RW")
    currency = models.CharField(max_length=10, default="USD")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

# User model

class User(AbstractBaseUser, PermissionsMixin):
    DEPARTMENT_CHOICES = (
        ("RETAIL", "RETAIL"),
        ("WHOLESALE", "WHOLESALE"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=255)
    department = models.CharField(
        max_length=20,
        choices=DEPARTMENT_CHOICES,
        default="WHOLESALE",
    )
    firebase_token = models.CharField(max_length=1024, null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    is_super_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CUSTOMUserManager()

    def save(self, *args, **kwargs):
        if not self.user_code:
            count = User.objects.count() + 1
            self.user_code = f"USERCODE{str(count).zfill(4)}"
        if not self.password.startswith("pbkdf2_"):
            self.set_password(self.password)
        super().save(*args, **kwargs)

    def compare_password(self, raw_password):
        return check_password(raw_password, self.password)


class UserTenant(models.Model):
    ROLE_CHOICES = (
        ("OWNER", "OWNER"),
        ("ADMIN", "ADMIN"),
        ("CASHIER", "CASHIER"),
        ("STORE_KEEPER", "STORE_KEEPER"),
        ("ACCOUNTANT", "ACCOUNTANT"),
        ("PHARMACIST", "PHARMACIST"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_tenants")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=255, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"PasswordResetToken({self.user.email})"


class RetailWholesaleRequest(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("OWNER_APPROVED", "Owner Approved"),
        ("STOCK_CONFIRMED", "Stock Confirmed"),
        ("PHARMACIST_APPROVED", "Pharmacist Approved"),
        ("AWAITING_PAYMENT", "Awaiting Payment"),
        ("PAID_PENDING_CONFIRMATION", "Paid Pending Confirmation"),
        ("PAYMENT_CONFIRMED", "Payment Confirmed"),
        ("READY_FOR_DELIVERY", "Ready For Delivery"),
        ("DELIVERED", "Delivered"),
        ("COMPLETED", "Completed"),
        ("REJECTED", "Rejected"),
    )
    PAYMENT_OPTION_CHOICES = (
        ("FULL", "Full Payment"),
        ("PARTIAL", "Partial Payment"),
        ("CREDIT", "Credit"),
    )
    PAYMENT_METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("CARD", "Card"),
        ("UPI", "UPI"),
        ("MOBILE_MONEY", "Mobile Money"),
        ("BANK_TRANSFER", "Bank Transfer"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    retail_tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="retail_outgoing_requests",
    )
    wholesale_tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="wholesale_incoming_requests",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_wholesale_requests_created",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="PENDING")
    payment_option = models.CharField(max_length=20, choices=PAYMENT_OPTION_CHOICES, default="FULL")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="BANK_TRANSFER")
    declared_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True, null=True)
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_wholesale_requests_decided",
    )
    wholesale_sale = models.ForeignKey(
        "Sale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_wholesale_source_requests",
    )
    retail_procurement_expense = models.ForeignKey(
        "Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_wholesale_source_requests",
    )
    decision_note = models.TextField(blank=True, null=True)
    decided_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RetailWholesaleRequestItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        RetailWholesaleRequest,
        on_delete=models.CASCADE,
        related_name="items",
    )
    medicine = models.ForeignKey("Medicine", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()


class SupportTicket(models.Model):
    TYPE_CHOICES = (
        ("bug", "Bug"),
        ("feature", "Feature"),
    )
    PRIORITY_CHOICES = (
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    )
    STATUS_CHOICES = (
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField()
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title} ({self.type})"


class Notification(models.Model):
    """Notifications for a tenant or a specific user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification({self.tenant.name}): {self.title[:50]}"


class Medicine(models.Model):
    """Represents a medicine/product in a tenant's inventory."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_created_medicines",
    )
    brand_name = models.CharField(max_length=255)
    generic_name = models.CharField(max_length=255, null=True, blank=True)
    manufacturer = models.CharField(max_length=255, null=True, blank=True)
    category = models.CharField(max_length=255, null=True, blank=True)
    unit = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.brand_name} ({self.tenant.name})"


class StockBatch(models.Model):
    """A stock batch for a medicine (tracks expiry and quantities)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name='batches')
    source_request = models.ForeignKey(
        RetailWholesaleRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivered_stock_batches",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retail_created_stock_batches",
    )
    batch_number = models.CharField(max_length=100)
    quantity = models.IntegerField(default=0)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    manufacture_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    supplier_name = models.CharField(max_length=255, null=True, blank=True)
    supplier_phone = models.CharField(max_length=50, null=True, blank=True)
    supplier_address = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.medicine.brand_name} - {self.batch_number}"


class Sale(models.Model):
    """Represents a sale/invoice created by cashier."""
    STATUS_CHOICES = (
        ("PENDING", "Pending approval"),
        ("APPROVED", "Approved by storekeeper"),
        ("REJECTED", "Rejected by storekeeper"),
        ("COMPLETED", "Completed/Paid"),
        ("CANCELLED", "Cancelled"),
    )
    PAYMENT_OPTION_CHOICES = (
        ("FULL", "Full Payment"),
        ("PARTIAL", "Partial Payment"),
        ("CREDIT", "Credit"),
    )
    PAYMENT_METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("CARD", "Card"),
        ("UPI", "UPI"),
        ("MOBILE_MONEY", "Mobile Money"),
        ("BANK_TRANSFER", "Bank Transfer"),
    )
    APPROVAL_STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sales')
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_sales')
    invoice_number = models.CharField(max_length=50, unique=True)
    customer_name = models.CharField(max_length=255, null=True, blank=True)
    customer_phone = models.CharField(max_length=50, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    
    payment_option = models.CharField(max_length=20, choices=PAYMENT_OPTION_CHOICES, default="FULL")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="CASH")
    currency = models.CharField(max_length=10, default="USD")
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)

    owner_approval_status = models.CharField(
        max_length=20, choices=APPROVAL_STATUS_CHOICES, default="PENDING"
    )
    owner_approved_at = models.DateTimeField(null=True, blank=True)
    owner_approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owner_approved_sales",
    )

    pharmacist_approval_status = models.CharField(
        max_length=20, choices=APPROVAL_STATUS_CHOICES, default="PENDING"
    )
    pharmacist_approved_at = models.DateTimeField(null=True, blank=True)
    pharmacist_approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pharmacist_approved_sales",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_sales')

    def save(self, *args, **kwargs):
        # Keep historical invoice currency stable even if tenant currency changes later.
        if self.currency:
            self.currency = str(self.currency).strip().upper()
        elif self.tenant_id:
            tenant_currency = getattr(self.tenant, "currency", None)
            self.currency = (tenant_currency or "USD").strip().upper()
        else:
            self.currency = "USD"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Sale {self.invoice_number} - {self.customer_name or 'No customer'}"


class SaleItem(models.Model):
    """Individual items in a sale."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.medicine.brand_name} x {self.quantity} in Sale {self.sale.invoice_number}"


class PartialPaymentReminderConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="partial_payment_reminder_configs")
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name="reminder_config")
    due_date = models.DateField()
    reminder_days_before = models.JSONField(default=list, blank=True)
    auto_send_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=True)
    customer_email = models.EmailField(null=True, blank=True)
    customer_phone = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_reminder_configs")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="updated_reminder_configs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"ReminderConfig({self.sale.invoice_number})"


class PartialPaymentReminderEvent(models.Model):
    CHANNEL_CHOICES = (
        ("EMAIL", "Email"),
        ("SMS", "SMS"),
    )
    MODE_CHOICES = (
        ("AUTO", "Auto"),
        ("MANUAL", "Manual"),
    )
    STATUS_CHOICES = (
        ("SENT", "Sent"),
        ("FAILED", "Failed"),
        ("SKIPPED", "Skipped"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="partial_payment_reminder_events")
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="reminder_events")
    config = models.ForeignKey(PartialPaymentReminderConfig, on_delete=models.CASCADE, related_name="events")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="SENT")
    scheduled_for = models.DateField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    recipient = models.CharField(max_length=255, null=True, blank=True)
    message = models.TextField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_partial_payment_reminders")

    def __str__(self):
        return f"ReminderEvent({self.sale.invoice_number}, {self.channel}, {self.mode}, {self.status})"


class ExpenseCategory(models.Model):
    """Categories for expenses (per tenant)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='expense_categories')
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class Expense(models.Model):
    """Expense record for a tenant."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(null=True, blank=True)
    expense_date = models.DateField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Expense {self.id} - {self.amount} ({self.tenant.name})"


class TenantSubscription(models.Model):
    STATUS_CHOICES = (
        ("TRIAL", "Trial"),
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("CANCELLED", "Cancelled"),
    )

    BILLING_CYCLE_CHOICES = (
        ("monthly", "Monthly"),
        ("annual", "Annual"),
    )

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="subscription")
    plan_id = models.CharField(max_length=50, default="starter")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="TRIAL")
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CYCLE_CHOICES, default="monthly")
    trial_end_date = models.DateField(null=True, blank=True)
    subscription_start_date = models.DateField(null=True, blank=True)
    subscription_end_date = models.DateField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Subscription({self.tenant.name}, {self.plan_id}, {self.status})"


class SubscriptionEvent(models.Model):
    ACTION_CHOICES = (
        ("UPGRADE", "Upgrade"),
        ("DOWNGRADE", "Downgrade"),
        ("CANCEL", "Cancel"),
        ("RENEW_TRIAL", "Renew Trial"),
        ("PAYMENT", "Payment"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="subscription_events")
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    from_plan_id = models.CharField(max_length=50, null=True, blank=True)
    to_plan_id = models.CharField(max_length=50, null=True, blank=True)
    payment_method = models.CharField(max_length=50, null=True, blank=True)
    promo_code = models.CharField(max_length=100, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} ({self.tenant.name})"


class SubscriptionPaymentTransaction(models.Model):
    PROVIDER_CHOICES = (
        ("MTN_MOMO", "MTN MoMo"),
        ("LANARI_PAY", "Lanari Pay"),
    )
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("SUCCESS", "Success"),
        ("FAILED", "Failed"),
        ("EXPIRED", "Expired"),
        ("CANCELLED", "Cancelled"),
    )
    BILLING_CYCLE_CHOICES = (
        ("monthly", "Monthly"),
        ("annual", "Annual"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="subscription_payment_transactions")
    plan_id = models.CharField(max_length=50)
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CYCLE_CHOICES, default="monthly")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="RWF")
    payment_method = models.CharField(max_length=50, default="mtn_momo")
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES, default="MTN_MOMO")
    phone_number = models.CharField(max_length=50, null=True, blank=True)
    reference_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    external_id = models.CharField(max_length=120, null=True, blank=True)
    provider_status = models.CharField(max_length=50, null=True, blank=True)
    provider_transaction_id = models.CharField(max_length=120, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    failure_reason = models.TextField(null=True, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    callback_payload = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_subscription_payments")
    event = models.ForeignKey(SubscriptionEvent, on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_transactions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SubscriptionPayment({self.tenant.name}, {self.plan_id}, {self.status})"


class SubscriptionPlan(models.Model):
    BUSINESS_TYPE_CHOICES = (
        ("RETAIL", "Retail"),
        ("WHOLESALE", "Wholesale"),
        ("BOTH", "Both"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    business_type = models.CharField(max_length=20, choices=BUSINESS_TYPE_CHOICES, default="BOTH")
    max_users = models.IntegerField(default=1)
    max_branches = models.IntegerField(default=1)
    features = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class AuditLog(models.Model):
    STATUS_CHOICES = (
        ("SUCCESS", "Success"),
        ("ERROR", "Error"),
        ("FAILED", "Failed"),
    )
    ACTION_CHOICES = (
        ("VIEW", "View"),
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    entity = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="SUCCESS")
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=64, null=True, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} {self.entity} ({self.status})"


class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    data = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key
