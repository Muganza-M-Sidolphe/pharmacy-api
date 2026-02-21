import base64
import json
import os
import uuid
from urllib import error, request


class MtnMomoError(Exception):
    pass


def _require_env(key):
    value = os.getenv(key, "").strip()
    if not value:
        raise MtnMomoError(f"Missing required env: {key}")
    return value


def _json_request(method, url, headers=None, payload=None):
    body = None
    req_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8") if response.readable() else ""
            return response.status, json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        detail = raw or str(exc)
        raise MtnMomoError(f"MTN API HTTP {exc.code}: {detail}")
    except error.URLError as exc:
        raise MtnMomoError(f"MTN API unreachable: {exc.reason}")


def _get_access_token():
    base_url = _require_env("MTN_MOMO_BASE_URL").rstrip("/")
    api_user = _require_env("MTN_MOMO_API_USER")
    api_key = _require_env("MTN_MOMO_API_KEY")
    subscription_key = _require_env("MTN_MOMO_COLLECTION_PRIMARY_KEY")

    credentials = base64.b64encode(f"{api_user}:{api_key}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {credentials}",
        "Ocp-Apim-Subscription-Key": subscription_key,
    }
    status_code, payload = _json_request("POST", f"{base_url}/collection/token/", headers=headers)
    if status_code not in (200, 201):
        raise MtnMomoError("Failed to get MTN access token")
    token = payload.get("access_token")
    if not token:
        raise MtnMomoError("MTN token response missing access_token")
    return token


def initiate_collection(amount, currency, phone_number, external_id, callback_url=None, payer_message="", payee_note=""):
    base_url = _require_env("MTN_MOMO_BASE_URL").rstrip("/")
    subscription_key = _require_env("MTN_MOMO_COLLECTION_PRIMARY_KEY")
    target_environment = os.getenv("MTN_MOMO_TARGET_ENVIRONMENT", "sandbox").strip() or "sandbox"

    token = _get_access_token()
    reference_id = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Reference-Id": reference_id,
        "X-Target-Environment": target_environment,
        "Ocp-Apim-Subscription-Key": subscription_key,
        "Content-Type": "application/json",
    }
    if callback_url:
        headers["X-Callback-Url"] = callback_url

    payload = {
        "amount": str(amount),
        "currency": currency,
        "externalId": external_id,
        "payer": {
            "partyIdType": "MSISDN",
            "partyId": phone_number,
        },
        "payerMessage": payer_message or "Subscription payment",
        "payeeNote": payee_note or "Pharmacy API subscription",
    }
    status_code, response_payload = _json_request(
        "POST",
        f"{base_url}/collection/v1_0/requesttopay",
        headers=headers,
        payload=payload,
    )
    if status_code not in (200, 201, 202):
        raise MtnMomoError("MTN request-to-pay failed")

    return {
        "reference_id": reference_id,
        "response_payload": response_payload,
    }


def get_collection_status(reference_id):
    base_url = _require_env("MTN_MOMO_BASE_URL").rstrip("/")
    subscription_key = _require_env("MTN_MOMO_COLLECTION_PRIMARY_KEY")
    target_environment = os.getenv("MTN_MOMO_TARGET_ENVIRONMENT", "sandbox").strip() or "sandbox"

    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Target-Environment": target_environment,
        "Ocp-Apim-Subscription-Key": subscription_key,
    }
    _, payload = _json_request(
        "GET",
        f"{base_url}/collection/v1_0/requesttopay/{reference_id}",
        headers=headers,
    )
    return payload
