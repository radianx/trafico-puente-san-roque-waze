import os
import json
import base64
import logging
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import pywebpush as pywebpush_module
from pywebpush import webpush, WebPushException

import config
from database import db_enabled, db_conn

try:
    from psycopg2.extras import Json, RealDictCursor
except ImportError:
    Json = None
    RealDictCursor = None

# --- Patch de compatibilidad para pywebpush / cryptography ---
# `pywebpush==1.14.0` llama a `ec.generate_private_key(ec.SECP256R1, ...)`
# pero modern `cryptography` espera una instancia `ec.SECP256R1()` (objeto), no la clase.
try:
    _orig_generate_private_key = pywebpush_module.ec.generate_private_key
    def _patched_generate_private_key(curve, backend):
        if curve == ec.SECP256R1:
            return _orig_generate_private_key(ec.SECP256R1(), backend)
        return _orig_generate_private_key(curve, backend)
    pywebpush_module.ec.generate_private_key = _patched_generate_private_key
except Exception as patch_ex:
    logging.warning("No se pudo aplicar el patch de compatibilidad para pywebpush: %s", patch_ex)


def _generate_vapid_keys():
    """Genera un nuevo par de claves VAPID y lo guarda en DB o archivos locales."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    with open(config.PRIVATE_KEY_FILE, "wb") as f:
        f.write(pem)
    raw_pub = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
    b64_pub = base64.urlsafe_b64encode(raw_pub).decode('utf-8').rstrip('=')

    if db_enabled():
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO push_config (config_key, private_key_pem, public_key, updated_at)
                    VALUES ('vapid', %s, %s, NOW())
                    ON CONFLICT (config_key)
                    DO UPDATE SET
                        private_key_pem = EXCLUDED.private_key_pem,
                        public_key = EXCLUDED.public_key,
                        updated_at = NOW();
                    """,
                    (pem.decode("utf-8"), b64_pub),
                )
                # Las suscripciones quedan inválidas al rotar claves VAPID.
                cur.execute("DELETE FROM push_subscriptions;")
            conn.commit()
    else:
        with open(config.PUBLIC_KEY_FILE, "w") as f:
            f.write(b64_pub)
        # Clear old subscriptions — they're bound to the old key
        if os.path.exists(config.SUBSCRIPTIONS_FILE):
            os.remove(config.SUBSCRIPTIONS_FILE)

    logging.info("Claves VAPID generadas y guardadas exitosamente.")


def init_vapid():
    """Inicializa claves VAPID en DB o filesystem."""
    if db_enabled():
        try:
            with db_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT private_key_pem, public_key FROM push_config WHERE config_key='vapid';"
                    )
                    row = cur.fetchone()
            if not row:
                logging.info("No hay claves VAPID en DB. Generando nuevas claves...")
                _generate_vapid_keys()
                return
            serialization.load_pem_private_key(
                row["private_key_pem"].encode("utf-8"),
                password=None,
            )
        except Exception:
            logging.warning("Clave VAPID en DB incompatible o faltante. Regenerando...")
            _generate_vapid_keys()
        return

    if not os.path.exists(config.PRIVATE_KEY_FILE) or not os.path.exists(config.PUBLIC_KEY_FILE):
        logging.info("Generando nuevas claves VAPID...")
        _generate_vapid_keys()
        return
    # Validate existing key is loadable
    try:
        with open(config.PRIVATE_KEY_FILE, "rb") as f:
            serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        logging.warning("Clave VAPID existente tiene formato incompatible. Regenerando...")
        try:
            os.remove(config.PRIVATE_KEY_FILE)
        except Exception:
            pass
        if os.path.exists(config.PUBLIC_KEY_FILE):
            try:
                os.remove(config.PUBLIC_KEY_FILE)
            except Exception:
                pass
        _generate_vapid_keys()


def fetch_vapid_public_key():
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT public_key FROM push_config WHERE config_key='vapid';")
                row = cur.fetchone()
                return row["public_key"] if row else None
    if os.path.exists(config.PUBLIC_KEY_FILE):
        with open(config.PUBLIC_KEY_FILE, "r") as f:
            return f.read().strip()
    return None


def get_vapid_private_key_for_webpush():
    """
    Returns the VAPID private key in the format pywebpush expects:
    - A base64url-encoded DER string (when loaded from DB)
    - A file path to the PEM file (when using local fallback)
    pywebpush's Vapid.from_string() expects DER base64url, NOT a PEM string.
    """
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT private_key_pem FROM push_config WHERE config_key='vapid';")
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("VAPID private key not found in DB")
        # Convert PEM → DER → base64url (what pywebpush's Vapid.from_string expects)
        private_key = serialization.load_pem_private_key(
            row["private_key_pem"].encode("utf-8"),
            password=None,
        )
        der_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return base64.urlsafe_b64encode(der_bytes).decode("utf-8")
    return config.PRIVATE_KEY_FILE


def load_subscriptions():
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT endpoint, p256dh, auth, threshold, direction, last_notified_value
                    FROM push_subscriptions
                    ORDER BY created_at ASC;
                """)
                rows = cur.fetchall()
                return [
                    {
                        "endpoint": r["endpoint"],
                        "keys": {"p256dh": r["p256dh"], "auth": r["auth"]},
                        "threshold": r["threshold"],
                        "direction": r["direction"],
                        "last_notified_value": r["last_notified_value"],
                    }
                    for r in rows
                ]
    if os.path.exists(config.SUBSCRIPTIONS_FILE):
        try:
            with open(config.SUBSCRIPTIONS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error("Error al leer %s: %s", config.SUBSCRIPTIONS_FILE, e)
    return []


def save_subscriptions(subs):
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM push_subscriptions;")
                for s in subs:
                    keys = s.get("keys") or {}
                    cur.execute(
                        """
                        INSERT INTO push_subscriptions (
                            endpoint, p256dh, auth, threshold, direction, last_notified_value, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, NOW());
                        """,
                        (
                            s.get("endpoint"),
                            keys.get("p256dh"),
                            keys.get("auth"),
                            int(s.get("threshold", 60)),
                            s.get("direction", "ida"),
                            Json(s.get("last_notified_value")) if Json is not None else json.dumps(s.get("last_notified_value")),
                        ),
                    )
            conn.commit()
        return
    try:
        with open(config.SUBSCRIPTIONS_FILE, 'w') as f:
            json.dump(subs, f, indent=2)
    except Exception as e:
        logging.error("Error al escribir %s: %s", config.SUBSCRIPTIONS_FILE, e)


def send_push_notification(subscription, title, body):
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": subscription["keys"]
            },
            data=json.dumps({
                "title": title,
                "body": body
            }),
            vapid_private_key=get_vapid_private_key_for_webpush(),
            vapid_claims={"sub": "mailto:admin@puentehoy.com"},
        )
        logging.info("Notificación push enviada con éxito a %s", subscription["endpoint"])
        return True
    except WebPushException as ex:
        resp_body = ""
        if ex.response is not None:
            try:
                resp_body = ex.response.text
            except Exception:
                pass
        logging.error(
            "Error de WebPush al enviar notificación: %s | status=%s | body=%s",
            ex,
            getattr(ex.response, "status_code", "N/A"),
            resp_body,
        )
        return False
    except Exception as e:
        logging.exception("Error genérico al enviar notificación push")
        return False
