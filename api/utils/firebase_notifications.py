import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:  # pragma: no cover
    firebase_admin = None
    credentials = None
    messaging = None

_firebase_app = None


def _init_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    if not getattr(settings, "FIREBASE_ENABLED", False):
        logger.debug("Firebase push notifications are disabled in settings.")
        return None

    if firebase_admin is None or credentials is None:
        logger.warning("firebase-admin is not installed. Firebase push notifications are disabled.")
        return None

    service_account_path = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    if not service_account_path:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_PATH is not configured. Firebase push notifications are disabled.")
        return None

    if not os.path.exists(service_account_path):
        logger.warning("Firebase service account path does not exist: %s", service_account_path)
        return None

    try:
        cred = credentials.Certificate(service_account_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase application initialized for push notifications.")
        return _firebase_app
    except Exception as exc:
        logger.exception("Failed to initialize Firebase app: %s", exc)
        return None


def send_firebase_notification(registration_token, title, body, data=None):
    app = _init_firebase_app()
    if not app:
        return False

    if not registration_token:
        logger.debug("No registration token provided for Firebase push notification.")
        return False

    message_data = data or {}

    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=registration_token,
            data={k: str(v) for k, v in message_data.items()},
        )
        messaging.send(message)
        logger.info("Firebase push sent to token %s", registration_token)
        return True
    except Exception as exc:
        logger.exception("Failed to send Firebase notification: %s", exc)
        return False
