#!/bin/bash

# Update OpenAPI Spec and Push to Apidog
# Run with: bash update_apidog.sh

set -e

echo "🔄 Updating OpenAPI Specification..."
echo "======================================"

# Activate virtual environment
source venv/bin/activate

# Generate OpenAPI spec
echo "📝 Generating OpenAPI JSON..."
python manage.py spectacular --format openapi-json --file openapi.json

if [ $? -eq 0 ]; then
    echo "✅ OpenAPI spec generated successfully!"
else
    echo "❌ Failed to generate OpenAPI spec"
    exit 1
fi

# Set Apidog token
export APIDOG_TOKEN='APS-hyZOCwQnuqYjI6PpQB35ELV2th2JbJ1U'

# Push to Apidog
echo ""
echo "🚀 Pushing to Apidog..."
python scripts/push_openapi_to_apidog.py --file openapi.json --project-id 1193908

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Successfully updated Apidog!"
    echo ""
    echo "📊 Summary:"
    echo "  - OpenAPI spec: openapi.json"
    echo "  - Apidog Project: 1193908"
    echo "  - Status: Updated"
else
    echo "❌ Failed to push to Apidog"
    exit 1
fi

echo ""
echo "🎉 Done!"
