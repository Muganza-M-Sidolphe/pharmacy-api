"""Push OpenAPI spec to Apidog (helper script with retries and logging).

This script supports two modes:
 1) Push the generated local `openapi.json` file to Apidog via an upload API.
 2) Ask Apidog to import from the running server by supplying the schema URL.

You must provide the correct Apidog API endpoint and an API token. Replace the placeholder endpoint/parameters below with the values from your Apidog account.

Usage examples:
  # Push local file
  export APIDOG_API_URL=https://api.apidog.com/api/v1/projects/{projectId}/import
  export APIDOG_TOKEN=your-api-key
  python3 scripts/push_openapi_to_apidog.py --file openapi.json --project-id 12345

  # Tell Apidog to import from your running server's schema URL
  python3 scripts/push_openapi_to_apidog.py --url http://localhost:8000/api/schema/ --project-id 12345

Environment variables:
  APIDOG_API_URL: Base import endpoint (default: https://api.apidog.com/v1/projects/{projectId}/import-openapi)
  APIDOG_TOKEN: Bearer token (Personal Access Token from Apidog, NOT API key)
  APIDOG_RETRY_COUNT: Number of retries on failure (default: 3)
  APIDOG_RETRY_DELAY: Seconds between retries (default: 2)

Note: Get your Personal Access Token from Apidog account settings, not the API key!
"""

import os
import argparse
import json
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
APIDOG_API_URL = os.getenv('APIDOG_API_URL', 'https://api.apidog.com/v1/projects/{projectId}/import-openapi')
APIDOG_TOKEN = os.getenv('APIDOG_TOKEN', '')
RETRY_COUNT = int(os.getenv('APIDOG_RETRY_COUNT', 3))
RETRY_DELAY = int(os.getenv('APIDOG_RETRY_DELAY', 2))
ENDPOINT_OVERWRITE_BEHAVIOR = os.getenv('APIDOG_ENDPOINT_OVERWRITE_BEHAVIOR', 'OVERWRITE_EXISTING')
SCHEMA_OVERWRITE_BEHAVIOR = os.getenv('APIDOG_SCHEMA_OVERWRITE_BEHAVIOR', 'OVERWRITE_EXISTING')

def create_session_with_retries():
    """Create a requests session with automatic retries on network errors."""
    session = requests.Session()
    retry_strategy = Retry(
        total=RETRY_COUNT,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_headers():
    """Build request headers with authorization.
    
    Apidog uses Bearer token authorization (Personal Access Token from account settings).
    """
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Apidog-Api-Version': '2024-03-28',  # Required by Apidog API
    }
    if APIDOG_TOKEN:
        # Apidog requires Bearer token format (Personal Access Token)
        if APIDOG_TOKEN.startswith('Bearer '):
            headers['Authorization'] = APIDOG_TOKEN
        else:
            headers['Authorization'] = f'Bearer {APIDOG_TOKEN}'
    return headers


def validate_token_or_exit():
    """Apidog import endpoint expects Personal Access Token, not APS API key."""
    if not APIDOG_TOKEN:
        logger.error("APIDOG_TOKEN is required.")
        raise SystemExit(1)

    raw_token = APIDOG_TOKEN.replace('Bearer ', '')
    if raw_token.startswith('APS-'):
        logger.error(
            "APIDOG_TOKEN looks like an API Key (APS-...). "
            "Use a Personal Access Token from Apidog account settings."
        )
        raise SystemExit(1)


def push_file(openapi_path, project_id):
    """Upload local openapi.json file to Apidog with retry logic.
    
    Apidog requires a JSON body with 'input' (URL or spec string) and optional 'options'.
    """
    if not os.path.exists(openapi_path):
        logger.error(f"Spec file not found: {openapi_path}")
        raise SystemExit(1)

    endpoint = APIDOG_API_URL.replace('{projectId}', str(project_id))
    logger.info(f"Uploading {openapi_path} to {endpoint}")
    logger.debug(f"Using Bearer token authorization with X-Apidog-Api-Version header")

    try:
        with open(openapi_path, 'r') as f:
            spec_content = f.read()
            logger.debug(f"Loaded spec with {len(spec_content)} bytes")

        session = create_session_with_retries()
        
        # Build request body according to Apidog API format
        # IMPORTANT: spec must be sent as a STRING, not an object
        payload = {
            "input": {
                "data": spec_content  # Send as string
            },
            "options": {
                "endpointOverwriteBehavior": ENDPOINT_OVERWRITE_BEHAVIOR,
                "schemaOverwriteBehavior": SCHEMA_OVERWRITE_BEHAVIOR,
                "deleteUnmatchedResources": False,
                "updateFolderOfChangedEndpoint": True,
                "prependBasePath": False
            }
        }

        response = session.post(
            endpoint,
            headers=get_headers(),
            json=payload,
            timeout=30
        )

        logger.debug(f"Response status: {response.status_code}")
        logger.debug(f"Response headers: {dict(response.headers)}")
        if response.text:
            logger.debug(f"Response body (first 500 chars): {response.text[:500]}")

        if response.status_code in [200, 201]:
            logger.info(f"✓ Successfully uploaded spec to Apidog (status {response.status_code})")
            
            try:
                result = response.json()
                if 'data' in result and 'counters' in result['data']:
                    counters = result['data']['counters']
                    logger.info(
                        "Import stats: %s endpoints created, %s updated, %s schemas created",
                        counters.get('endpointCreated', 0),
                        counters.get('endpointUpdated', 0),
                        counters.get('schemaCreated', 0),
                    )
                logger.debug(f"Full response: {result}")
                return result
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"Response is not valid JSON")
                logger.info(f"Response: {response.text[:200]}")
                return {"status": "success"}
        else:
            logger.error(f"✗ Upload failed (status {response.status_code})")
            if response.text:
                try:
                    error_data = response.json()
                    logger.error(f"Error response: {json.dumps(error_data, indent=2)}")
                except:
                    logger.error(f"Response: {response.text[:500]}")
            response.raise_for_status()

    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Request failed: {e}")
        raise SystemExit(1)
    except json.JSONDecodeError as e:
        logger.error(f"✗ Invalid JSON in openapi.json: {e}")
        raise SystemExit(1)
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        raise SystemExit(1)


def import_from_url(schema_url, project_id):
    """Ask Apidog to import the schema directly from schema_url with retry logic.
    
    Uses the Apidog API import-openapi endpoint with URL input.
    """
    endpoint = APIDOG_API_URL.replace('{projectId}', str(project_id))
    logger.info(f"Requesting Apidog to import schema from {schema_url} into project {project_id}")

    try:
        session = create_session_with_retries()
        
        # Build request body according to Apidog API format
        payload = {
            "input": {
                "url": schema_url
            },
            "options": {
                "endpointOverwriteBehavior": ENDPOINT_OVERWRITE_BEHAVIOR,
                "schemaOverwriteBehavior": SCHEMA_OVERWRITE_BEHAVIOR,
                "deleteUnmatchedResources": False,
                "updateFolderOfChangedEndpoint": False,
                "prependBasePath": False
            }
        }

        response = session.post(
            endpoint,
            headers=get_headers(),
            json=payload,
            timeout=30
        )

        logger.debug(f"Response status: {response.status_code}")

        if response.status_code in [200, 201]:
            logger.info(f"✓ Successfully imported schema from URL (status {response.status_code})")
            
            try:
                result = response.json()
                if 'data' in result and 'counters' in result['data']:
                    counters = result['data']['counters']
                    logger.info(
                        "Import stats: %s endpoints created, %s updated, %s schemas created",
                        counters.get('endpointCreated', 0),
                        counters.get('endpointUpdated', 0),
                        counters.get('schemaCreated', 0),
                    )
                logger.debug(f"Full response: {result}")
                return result
            except (json.JSONDecodeError, ValueError):
                return {"status": "success"}
        else:
            logger.error(f"✗ Import failed (status {response.status_code})")
            if response.text:
                try:
                    error_data = response.json()
                    logger.error(f"Error response: {json.dumps(error_data, indent=2)}")
                except:
                    logger.error(f"Response: {response.text[:500]}")
            response.raise_for_status()

    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Request failed: {e}")
        raise SystemExit(1)
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Push OpenAPI spec to Apidog',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--file', help='Local openapi.json file to upload')
    parser.add_argument('--url', help='Remote schema URL (e.g., http://localhost:8000/api/schema/)')
    parser.add_argument('--project-id', required=True, help='Apidog project ID to update')
    parser.add_argument('--token', help='Apidog Personal Access Token (overrides APIDOG_TOKEN env var)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if not APIDOG_TOKEN:
        logger.warning('APIDOG_TOKEN not set. Requests will be unauthenticated.')
    if not APIDOG_API_URL or APIDOG_API_URL == 'https://api.apidog.com/api/v1/projects/{projectId}/import':
        logger.warning('APIDOG_API_URL not customized. Update it to match your Apidog endpoint.')

    # Allow passing token on the CLI which overrides the environment variable
    if args.token:
        APIDOG_TOKEN = args.token
    validate_token_or_exit()

    try:
        if args.file:
            push_file(args.file, args.project_id)
        elif args.url:
            import_from_url(args.url, args.project_id)
        else:
            parser.error('Either --file or --url must be provided')
        logger.info('Done!')
    except SystemExit as e:
        if e.code != 0:
            logger.error('Script exited with error.')
        raise
