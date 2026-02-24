import json
import os
import socket
from urllib import error, request


class LanariPayError(Exception):
    pass


def _require_env(key):
    value = os.getenv(key, "").strip()
    if not value:
        raise LanariPayError(f"Missing required env: {key}")
    return value


def _json_request(method, url, headers=None, payload=None):
    body = None
    req_headers = {
        "Accept": "application/json",
        "User-Agent": "curl/8.5.0",
    }
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(
        url=url,
        data=body,
        headers=req_headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8") if response.readable() else ""
            return response.status, _parse_payload(raw)
    except (TimeoutError, socket.timeout):
        raise LanariPayError("Lanari API timeout: provider did not respond in time")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        detail = raw or str(exc)
        raise LanariPayError(f"Lanari API HTTP {exc.code}: {detail}")
    except error.URLError as exc:
        raise LanariPayError(f"Lanari API unreachable: {exc.reason}")


def _parse_payload(raw):
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except json.JSONDecodeError:
        return {"raw": raw}


def _extract_first(payload, keys):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def initiate_payment(
    amount,
    currency,
    phone_number,
    external_id,
    payout_numbers=None,
    customer_email=None,
    description=None,
):
    process_url = _require_env("LANARI_PAY_PROCESS_URL")
    api_key = _require_env("LANARI_PAY_API_KEY")
    api_secret = _require_env("LANARI_PAY_API_SECRET")

    headers = {
        "X-API-Key": api_key,
        "X-API-Secret": api_secret,
    }
    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "amount": float(amount),
        "currency": currency,
        "customer_phone": phone_number,
        "payment_method": "mobile_money",
        "description": description or f"Subscription payment ({external_id})",
        "external_id": external_id,
    }
    if customer_email:
        payload["customer_email"] = customer_email
    if payout_numbers:
        payload["payout_numbers"] = payout_numbers
    _, response_payload = _json_request("POST", process_url, headers=headers, payload=payload)

    reference_id = _extract_first(
        response_payload,
        [
            "transaction_ref",
            "transactionRef",
            "reference_id",
            "referenceId",
            "transaction_id",
            "transactionId",
            "id",
        ],
    )
    status_value = _extract_first(response_payload, ["status", "payment_status", "state"])

    return {
        "reference_id": str(reference_id) if reference_id is not None else external_id,
        "provider_status": str(status_value) if status_value is not None else "PENDING",
        "response_payload": response_payload,
    }


def get_payment_status(reference_id, external_id=None):
    status_url = _require_env("LANARI_PAY_STATUS_URL")
    api_key = _require_env("LANARI_PAY_API_KEY")
    api_secret = _require_env("LANARI_PAY_API_SECRET")

    headers = {
        "X-API-Key": api_key,
        "X-API-Secret": api_secret,
    }
    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "transaction_ref": reference_id,
    }
    if external_id:
        payload["external_id"] = external_id

    _, response_payload = _json_request("POST", status_url, headers=headers, payload=payload)
    return response_payload
