#!/usr/bin/env python3
"""
Validate Apidog credentials and test the import endpoint.

This script helps you verify your Apidog setup before running the full import.
"""

import os
import sys
import json
import requests
import argparse

def test_connectivity(project_id, token):
    """Test if we can connect to the Apidog API endpoint."""
    endpoint = f"https://api.apidog.com/v1/projects/{project_id}/import-openapi"
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Apidog-Api-Version': '2024-03-28',
        'Authorization': f'Bearer {token}' if not token.startswith('Bearer ') else token
    }
    
    print(f"Testing connection to Apidog...")
    print(f"  Endpoint: {endpoint}")
    print(f"  Auth: Bearer token (length: {len(token)}) chars")
    print(f"  Headers: X-Apidog-Api-Version: 2024-03-28")
    
    try:
        # Send minimal test payload
        test_payload = {
            "input": {"data": '{"openapi":"3.0.0","info":{"title":"Test","version":"1.0.0"},"paths":{}}'},
            "options": {"endpointOverwriteBehavior": "OVERWRITE_EXISTING"}
        }
        
        response = requests.post(
            endpoint,
            headers=headers,
            json=test_payload,
            timeout=10
        )
        
        print(f"\n✓ Connection successful!")
        print(f"  Status code: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  ✓ Authentication successful (200 OK)")
            try:
                data = response.json()
                print(f"  Response: {json.dumps(data, indent=2)[:200]}...")
                return True
            except:
                print(f"  Response (text): {response.text[:200]}")
                return True
        elif response.status_code == 401:
            print(f"  ✗ Authentication failed (401)")
            print(f"    Check that your token is a Personal Access Token (not API key)")
            return False
        elif response.status_code == 404:
            print(f"  ✗ Endpoint not found (404)")
            print(f"    Check that the endpoint URL is correct")
            return False
        else:
            print(f"  ! Unexpected status: {response.status_code}")
            if response.text:
                print(f"    Response: {response.text[:300]}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"  ✗ Connection failed: {e}")
        return False
    except Exception as e:
        print(f"  ✗ Unexpected error: {e}")
        return False

def validate_spec_file(filepath):
    """Validate that the OpenAPI spec file is valid JSON."""
    print(f"\nValidating spec file: {filepath}")
    
    if not os.path.exists(filepath):
        print(f"  ✗ File not found: {filepath}")
        return False
    
    try:
        with open(filepath, 'r') as f:
            spec = json.load(f)
        
        endpoints = len(spec.get('paths', {}))
        schemas = len(spec.get('components', {}).get('schemas', {}))
        
        print(f"  ✓ Valid OpenAPI spec")
        print(f"    Endpoints: {endpoints}")
        print(f"    Schemas: {schemas}")
        print(f"    Version: {spec.get('info', {}).get('version', 'unknown')}")
        print(f"    Title: {spec.get('info', {}).get('title', 'unknown')}")
        
        return True
    except json.JSONDecodeError as e:
        print(f"  ✗ Invalid JSON: {e}")
        return False
    except Exception as e:
        print(f"  ✗ Error reading file: {e}")
        return False

def validate_token_format(token):
    """Check if token format looks reasonable."""
    print(f"\nValidating token format...")
    
    if not token:
        print(f"  ✗ Token is empty")
        return False
    
    if token.startswith('APS-'):
        print(f"  ! Warning: Token looks like API key (APS-xxx)")
        print(f"    You need a Personal Access Token (pat_xxx or similar)")
        print(f"    Get it from Apidog account settings → Personal Access Token")
        return False
    
    if token.startswith('Bearer '):
        print(f"  ✓ Token has Bearer prefix (will be removed if present)")
        return True
    
    if len(token) > 20:
        print(f"  ✓ Token length looks reasonable ({len(token)} chars)")
        return True
    
    print(f"  ! Token format not recognized (length: {len(token)})")
    return True  # Still allow it, might be valid

def main():
    parser = argparse.ArgumentParser(
        description='Validate Apidog credentials and test connection'
    )
    parser.add_argument('--project-id', required=True, help='Apidog project ID')
    parser.add_argument('--token', help='Apidog Personal Access Token (or use APIDOG_TOKEN env var)')
    parser.add_argument('--spec-file', default='openapi.json', help='OpenAPI spec file to validate')
    args = parser.parse_args()
    
    # Get token from args or environment
    token = args.token or os.getenv('APIDOG_TOKEN', '')
    
    print("=" * 60)
    print("Apidog Credentials Validator")
    print("=" * 60)
    
    # Validate inputs
    if not args.project_id:
        print("✗ Project ID is required")
        return 1
    
    if not token:
        print("✗ No token provided. Use --token or set APIDOG_TOKEN env var")
        return 1
    
    all_valid = True
    
    # Run validations
    all_valid &= validate_token_format(token)
    all_valid &= validate_spec_file(args.spec_file)
    all_valid &= test_connectivity(args.project_id, token)
    
    print("\n" + "=" * 60)
    if all_valid:
        print("✓ All validations passed!")
        print("\nYou can now run:")
        print(f"  python3 scripts/push_openapi_to_apidog.py \\")
        print(f"    --file {args.spec_file} \\")
        print(f"    --project-id {args.project_id}")
        return 0
    else:
        print("✗ Some validations failed. See above for details.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
