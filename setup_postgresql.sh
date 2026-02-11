#!/bin/bash

# PostgreSQL Setup Script for Pharmacy API
# Run with: bash setup_postgresql.sh

set -e

echo "🐘 PostgreSQL Setup for Pharmacy API"
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo -e "${RED}Please do not run as root. Run as regular user with sudo access.${NC}"
   exit 1
fi

# Step 1: Install PostgreSQL
echo -e "${YELLOW}Step 1: Installing PostgreSQL...${NC}"
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev

# Step 2: Start PostgreSQL
echo -e "${YELLOW}Step 2: Starting PostgreSQL service...${NC}"
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Step 3: Get database credentials
echo ""
echo -e "${YELLOW}Step 3: Database Configuration${NC}"
read -p "Enter database name [pharmacy_db]: " DB_NAME
DB_NAME=${DB_NAME:-pharmacy_db}

read -p "Enter database user [pharmacy_user]: " DB_USER
DB_USER=${DB_USER:-pharmacy_user}

read -sp "Enter database password: " DB_PASSWORD
echo ""

# Step 4: Create database and user
echo -e "${YELLOW}Step 4: Creating database and user...${NC}"
sudo -u postgres psql <<EOF
CREATE DATABASE $DB_NAME;
CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
ALTER ROLE $DB_USER SET client_encoding TO 'utf8';
ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';
ALTER ROLE $DB_USER SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\q
EOF

# Step 5: Create .env file
echo -e "${YELLOW}Step 5: Creating .env file...${NC}"
cat > .env <<EOF
# Database Configuration
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=localhost
DB_PORT=5432

# Django Configuration
SECRET_KEY=django-insecure-dnf!!tltw99hr6e^_\$p5u^9y5323+-*5c0m5\$&%zi^)k+f28v5
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
EOF

# Step 6: Install Python dependencies
echo -e "${YELLOW}Step 6: Installing Python dependencies...${NC}"
source venv/bin/activate
pip install psycopg2-binary python-dotenv

# Step 7: Run migrations
echo -e "${YELLOW}Step 7: Running database migrations...${NC}"
python manage.py makemigrations
python manage.py migrate

# Step 8: Create superuser
echo ""
echo -e "${YELLOW}Step 8: Create Django superuser${NC}"
read -p "Do you want to create a superuser now? (y/n): " CREATE_SUPERUSER
if [ "$CREATE_SUPERUSER" = "y" ] || [ "$CREATE_SUPERUSER" = "Y" ]; then
    python manage.py createsuperuser
fi

echo ""
echo -e "${GREEN}✅ PostgreSQL setup completed successfully!${NC}"
echo ""
echo "Next steps:"
echo "1. Activate virtual environment: source venv/bin/activate"
echo "2. Start development server: python manage.py runserver 0.0.0.0:8000"
echo ""
echo "Database connection details:"
echo "  Database: $DB_NAME"
echo "  User: $DB_USER"
echo "  Host: localhost"
echo "  Port: 5432"
echo ""
echo "⚠️  IMPORTANT: Add .env to .gitignore to keep credentials secure!"
