# Pharmacy Management System API

Multi-tenant pharmacy management system built with Django REST Framework and PostgreSQL.

## 🚀 Features

- **Multi-tenant Architecture**: Support multiple pharmacies in one system
- **Role-Based Access Control**: Owner, Admin, Cashier, Storekeeper, Accountant, Pharmacist
- **Inventory Management**: Track medicines, batches, expiry dates
- **Sales Management**: Create and approve sales/invoices
- **Financial Reports**: Revenue, expenses, analytics
- **JWT Authentication**: Secure token-based authentication
- **RESTful API**: Complete REST API with OpenAPI documentation

## 🛠️ Tech Stack

- **Backend**: Django 6.0, Django REST Framework
- **Database**: PostgreSQL
- **Authentication**: JWT (Simple JWT)
- **API Documentation**: drf-spectacular (OpenAPI 3.0)
- **CORS**: django-cors-headers

## 📋 Prerequisites

- Python 3.12+
- PostgreSQL 12+
- pip and virtualenv

## ⚙️ Installation

### 1. Clone Repository
```bash
git clone <pharmacy-api>
cd pharmacy-api
```

### 2. Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Setup PostgreSQL
```bash
# Create database and user
sudo -i -u postgres psql
CREATE DATABASE pharmacy_db;
CREATE USER pharmacy_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE pharmacy_db TO pharmacy_user;
\q
```

### 5. Configure Environment Variables
```bash
cp .env.example .env

```

### 6. Run Migrations
```bash
python manage.py migrate
```

### 7. Create Superuser
```bash
python manage.py createsuperuser
```

### 8. Run Development Server
```bash
python manage.py runserver 0.0.0.0:8000
```

## 📚 API Documentation

- **Swagger UI**: http://localhost:8000/api/schema/swagger-ui/
- **ReDoc**: http://localhost:8000/api/schema/redoc/
- **OpenAPI JSON**: http://localhost:8000/openapi.json

## 🔑 Authentication

All authenticated endpoints require JWT token:

```bash
# Login
POST /api/login/
{
  "email": "user@example.com",
  "password": "password"
}

# Use token in headers
Authorization: Bearer <your-token>
```

## 📦 Main Endpoints

### Authentication
- `POST /api/login/` - User login
- `POST /api/change-password/` - Change password
- `POST /api/select-tenant/` - Select tenant (multi-tenant)

### Owner
- `POST /api/owner/create-user/` - Create user
- `GET /api/owner/users/` - List users
- `PUT /api/owner/users/{id}/` - Update user

### Storekeeper
- `POST /api/storekeeper/inventory/` - Add medicine
- `GET /api/storekeeper/inventory/` - List medicines
- `GET /api/storekeeper/expiry-alerts/` - Expiry alerts
- `POST /api/storekeeper/sales/{id}/approve/` - Approve sale

### Cashier
- `GET /api/cashier/dashboard/` - Dashboard
- `POST /api/cashier/sales/` - Create sale
- `GET /api/cashier/sales/list/` - List sales
- `GET /api/cashier/medicines/` - Available medicines

### Accountant
- `GET /api/accountant/invoices/` - List invoices
- `GET /api/accountant/reports/financial/` - Financial report
- `POST /api/accountant/expenses/` - Create expense
- `GET /api/accountant/analytics/` - Analytics dashboard

### Pharmacist
- `GET /api/pharmacist/invoices/` - List invoices
- `POST /api/pharmacist/invoices/{id}/approve/` - Approve invoice
- `GET /api/pharmacist/partial-payments/` - Partial payments

## 🗂️ Project Structure

```
pharmacy-api/
├── api/
│   ├── models.py           # Database models
│   ├── serializers.py      # DRF serializers
│   ├── views/              # API views (organized by role)
│   │   ├── auth/
│   │   ├── owner/
│   │   ├── cashier/
│   │   ├── storekeeper/
│   │   ├── accountant/
│   │   └── pharmacist/
│   ├── urls.py             # URL routing
│   └── admin.py            # Django admin
├── config/
│   ├── settings.py         # Django settings
│   └── urls.py             # Root URLs
├── scripts/
│   └── push_openapi_to_apidog.py
├── requirements.txt
├── .env.example
└── README.md
```

## 🧪 Testing

```bash
# Run tests
python manage.py test

# Check code coverage
coverage run --source='.' manage.py test
coverage report
```

## 🚢 Deployment

See `DEPLOYMENT.md` for production deployment instructions.

## 📖 Documentation

- [API Handoff Documentation](API_HANDOFF_DOCUMENTATION.md)
- [Frontend Quick Start](FRONTEND_QUICKSTART.md)
- [PostgreSQL Setup](POSTGRESQL_SETUP.md)

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit changes (`git commit -m 'Add AmazingFeature'`)
4. Push to branch (`git push origin feature/AmazingFeature`)
5. Open Pull Request

## 📝 License

This project is licensed under the MIT License.

## 👥 Authors

- Your Name - Initial work

## 🙏 Acknowledgments

- Django REST Framework
- PostgreSQL
- drf-spectacular
