from flask import Flask, jsonify, render_template, request, send_from_directory
import WazeRouteCalculator
import threading
import time
import logging
from datetime import datetime, timezone
import math
import os
import json
import base64
from contextlib import contextmanager
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import pywebpush as pywebpush_module
from pywebpush import webpush, WebPushException
try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except Exception:
    psycopg2 = None
    Json = None
    RealDictCursor = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PRIVATE_KEY_FILE = "private_key.pem"
PUBLIC_KEY_FILE = "public_key.txt"
SUBSCRIPTIONS_FILE = "subscriptions.json"
DB_RUNTIME_DISABLED = False
DB_RUNTIME_DISABLE_REASON = None


def get_db_url():
    return os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")


def db_enabled():
    return bool(get_db_url()) and psycopg2 is not None and not DB_RUNTIME_DISABLED


@contextmanager
def db_conn():
    global DB_RUNTIME_DISABLED, DB_RUNTIME_DISABLE_REASON
    url = get_db_url()
    if not url:
        raise RuntimeError("Supabase/Postgres URL is not configured")
    try:
        with psycopg2.connect(url) as conn:
            yield conn
    except Exception as exc:
        DB_RUNTIME_DISABLED = True
        DB_RUNTIME_DISABLE_REASON = str(exc)
        raise


def init_db():
    global DB_RUNTIME_DISABLED, DB_RUNTIME_DISABLE_REASON
    if not get_db_url():
        logging.info("SUPABASE_DB_URL/DATABASE_URL no configurada. Usando almacenamiento local de archivos.")
        return
    if psycopg2 is None:
        logging.warning("SUPABASE_DB_URL configurada pero psycopg2 no está instalado. Usando almacenamiento local.")
        return

    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS push_config (
                        config_key TEXT PRIMARY KEY,
                        private_key_pem TEXT NOT NULL,
                        public_key TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS push_subscriptions (
                        endpoint TEXT PRIMARY KEY,
                        p256dh TEXT NOT NULL,
                        auth TEXT NOT NULL,
                        threshold INTEGER NOT NULL,
                        direction TEXT NOT NULL,
                        last_notified_value JSONB NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS traffic_readings (
                        id             SERIAL PRIMARY KEY,
                        recorded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        ida_minutes    NUMERIC(5,1),
                        vuelta_minutes NUMERIC(5,1)
                    );
                """)
                # Agregar columnas para telemetría y auditoría de variables internas (historico)
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS waze_ida_raw NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS waze_vuelta_raw NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS visual_penalty_ida NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS visual_penalty_vuelta NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS active_booths NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS booth_correction NUMERIC(5,1);")
                cur.execute("ALTER TABLE traffic_readings ADD COLUMN IF NOT EXISTS guard_penalty NUMERIC(5,1);")
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS traffic_readings_recorded_at_idx
                        ON traffic_readings (recorded_at DESC);
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bridge_visuals (
                        id             SERIAL PRIMARY KEY,
                        recorded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        direction      TEXT NOT NULL,
                        penalty_minutes INTEGER NOT NULL DEFAULT 0,
                        raw_json       JSONB
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS bridge_visuals_recorded_at_idx
                        ON bridge_visuals (recorded_at DESC);
                """)
            conn.commit()
    except Exception as exc:
        DB_RUNTIME_DISABLED = True
        DB_RUNTIME_DISABLE_REASON = str(exc)
        logging.warning(
            "No se pudo conectar a Supabase/Postgres. Se activa fallback local. error=%s",
            exc,
        )

def _generate_vapid_keys():
    """Genera un nuevo par de claves VAPID y lo guarda en DB o archivos locales."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    with open(PRIVATE_KEY_FILE, "wb") as f:
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
        with open(PUBLIC_KEY_FILE, "w") as f:
            f.write(b64_pub)
        # Clear old subscriptions — they're bound to the old key
        if os.path.exists(SUBSCRIPTIONS_FILE):
            os.remove(SUBSCRIPTIONS_FILE)

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

    if not os.path.exists(PRIVATE_KEY_FILE) or not os.path.exists(PUBLIC_KEY_FILE):
        logging.info("Generando nuevas claves VAPID...")
        _generate_vapid_keys()
        return
    # Validate existing key is loadable
    try:
        with open(PRIVATE_KEY_FILE, "rb") as f:
            serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        logging.warning("Clave VAPID existente tiene formato incompatible. Regenerando...")
        os.remove(PRIVATE_KEY_FILE)
        if os.path.exists(PUBLIC_KEY_FILE):
            os.remove(PUBLIC_KEY_FILE)
        _generate_vapid_keys()


def fetch_vapid_public_key():
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT public_key FROM push_config WHERE config_key='vapid';")
                row = cur.fetchone()
                return row["public_key"] if row else None
    if os.path.exists(PUBLIC_KEY_FILE):
        with open(PUBLIC_KEY_FILE, "r") as f:
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
    return PRIVATE_KEY_FILE


def migrate_local_files_to_db_if_needed():
    if not db_enabled():
        return
    with db_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM push_config WHERE config_key='vapid';")
            has_cfg = bool(cur.fetchone())
            cur.execute("SELECT 1 FROM push_subscriptions LIMIT 1;")
            has_subs = bool(cur.fetchone())

    if has_cfg and has_subs:
        return

    migrated_any = False
    if not has_cfg and os.path.exists(PRIVATE_KEY_FILE) and os.path.exists(PUBLIC_KEY_FILE):
        with open(PRIVATE_KEY_FILE, "rb") as f:
            pem = f.read().decode("utf-8")
        with open(PUBLIC_KEY_FILE, "r") as f:
            pub = f.read().strip()
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO push_config (config_key, private_key_pem, public_key, updated_at)
                    VALUES ('vapid', %s, %s, NOW())
                    ON CONFLICT (config_key) DO NOTHING;
                    """,
                    (pem, pub),
                )
            conn.commit()
        migrated_any = True

    if not has_subs and os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                subs = json.load(f)
        except Exception:
            subs = []
        if subs:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    for s in subs:
                        keys = s.get("keys") or {}
                        cur.execute(
                            """
                            INSERT INTO push_subscriptions (
                                endpoint, p256dh, auth, threshold, direction, last_notified_value, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (endpoint)
                            DO UPDATE SET
                                p256dh = EXCLUDED.p256dh,
                                auth = EXCLUDED.auth,
                                threshold = EXCLUDED.threshold,
                                direction = EXCLUDED.direction,
                                last_notified_value = EXCLUDED.last_notified_value,
                                updated_at = NOW();
                            """,
                            (
                                s.get("endpoint"),
                                keys.get("p256dh"),
                                keys.get("auth"),
                                int(s.get("threshold", 60)),
                                s.get("direction", "ida"),
                                Json(s.get("last_notified_value")),
                            ),
                        )
                conn.commit()
            migrated_any = True

    if migrated_any:
        logging.info("Migración local->Supabase completada para push_config/push_subscriptions.")


init_db()
migrate_local_files_to_db_if_needed()
init_vapid()

# Compatibility patch:
# `pywebpush==1.14.0` calls `ec.generate_private_key(ec.SECP256R1, ...)`
# but modern `cryptography` expects `ec.SECP256R1()` (instance), not the class.
# We patch only this call to preserve the rest of pywebpush/http_ece behavior.
try:
    _orig_generate_private_key = pywebpush_module.ec.generate_private_key

    def _patched_generate_private_key(curve, backend):
        if curve is ec.SECP256R1:
            curve = ec.SECP256R1()
        return _orig_generate_private_key(curve, backend)

    pywebpush_module.ec.generate_private_key = _patched_generate_private_key
except Exception:
    pass

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
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error("Error al leer %s: %s", SUBSCRIPTIONS_FILE, e)
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
                            Json(s.get("last_notified_value")),
                        ),
                    )
            conn.commit()
        return
    try:
        with open(SUBSCRIPTIONS_FILE, 'w') as f:
            json.dump(subs, f, indent=2)
    except Exception as e:
        logging.error("Error al escribir %s: %s", SUBSCRIPTIONS_FILE, e)

def send_push_notification(subscription, title, body):
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                # Browser subscription provides exactly these fields.
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

from werkzeug.middleware.proxy_fix import ProxyFix

# Desactivar logs innecesarios de la librería de Waze
logging.getLogger("WazeRouteCalculator.WazeRouteCalculator").setLevel(logging.WARNING)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


@app.context_processor
def inject_globals():
    """Make current_year available in all templates."""
    return {"current_year": datetime.now().year}

# Configuración de Coordenadas y Región
POSADAS_COORDS = "-27.4035,-55.8928"
ENCARNACION_COORDS = "-27.3522,-55.8588"
REGION = 'EU'
UPDATE_INTERVAL_SECONDS = 5 * 60  # 5 minutos

# --- Penalización por cambio de guardia en Aduana Argentina ---
# Los cambios de guardia se realizan a las 07:00, 15:00 y 23:00 h (hora Argentina).
# Durante el traspaso de funciones, el ritmo de atención baja significativamente
# pero los sensores de tráfico (Waze/Google Maps) no registran este retraso
# porque los vehículos ya aparecen como "estacionados" cuando están detenidos.
GUARD_SHIFT_HOURS = [7, 15, 23]  # Horas de cambio de guardia (America/Argentina/Cordoba)
GUARD_SHIFT_MAX_PENALTY = 60     # Penalización máxima en minutos al momento exacto del cambio
GUARD_SHIFT_DECAY_MINUTES = 45   # Ventana en minutos donde la penalización decae a cero
GUARD_SHIFT_CONGESTION_THRESHOLD = 40  # Solo aplicar si la estimación base ya supera este umbral


def calculate_guard_shift_penalty(now_ar_hour, now_ar_minute, base_estimate_minutes):
    """
    Calcula la penalización por cambio de guardia en la Aduana Argentina.

    La penalización solo se aplica cuando:
    1. La estimación base ya muestra congestión (> GUARD_SHIFT_CONGESTION_THRESHOLD min)
    2. Estamos dentro de la ventana de GUARD_SHIFT_DECAY_MINUTES después de un cambio de guardia

    Usa una curva coseno (suave) para que la penalización sea máxima en :00 y
    decaiga gradualmente a cero durante los siguientes 45 minutos.

    Args:
        now_ar_hour: Hora actual en Argentina (0-23)
        now_ar_minute: Minuto actual en Argentina (0-59)
        base_estimate_minutes: Estimación actual en minutos (Waze + penalización visual)

    Returns:
        Penalización en minutos (float). 0 si no aplica.
    """
    if base_estimate_minutes <= GUARD_SHIFT_CONGESTION_THRESHOLD:
        return 0.0

    current_total_minutes = now_ar_hour * 60 + now_ar_minute

    min_elapsed = None
    for shift_hour in GUARD_SHIFT_HOURS:
        shift_total = shift_hour * 60
        # Minutos transcurridos desde el último cambio de guardia
        elapsed = (current_total_minutes - shift_total) % (24 * 60)
        if elapsed < GUARD_SHIFT_DECAY_MINUTES:
            if min_elapsed is None or elapsed < min_elapsed:
                min_elapsed = elapsed

    if min_elapsed is None:
        return 0.0

    # Curva coseno: máxima en 0, decae suavemente a 0 en GUARD_SHIFT_DECAY_MINUTES
    # cos(0) = 1.0, cos(π) = -1.0 → (1 + cos(x)) / 2 da una curva de 1.0 a 0.0
    decay_ratio = (1.0 + math.cos(math.pi * min_elapsed / GUARD_SHIFT_DECAY_MINUTES)) / 2.0
    penalty = GUARD_SHIFT_MAX_PENALTY * decay_ratio

    return round(penalty, 1)


# --- Estimación heurística de casillas abiertas en Aduana Argentina ---
# El Centro de Frontera Posadas opera entre 1 y 8 casillas simultáneas.
# La cantidad varía dinámicamente según hora, personal y volumen de tráfico.
# No se publica un número oficial; esta tabla es una estimación heurística
# basada en patrones operativos observados.
BOOTH_SCHEDULE = {
    # hora: casillas estimadas activas
    0: 1.5,  1: 1.5,  2: 1.5,  3: 1.5,  4: 1.5,  5: 2,
    6: 3,    7: 4,    8: 5,    9: 5.5,  10: 6,   11: 6,
    12: 6,   13: 6,   14: 6,   15: 6,   16: 6.5, 17: 6.5,
    18: 6,   19: 5,   20: 4,   21: 3,   22: 2.5, 23: 2,
}
BOOTH_REFERENCE = 8            # Casillas en horario pico. Los sensores están "calibrados" a este nivel.
BOOTH_BASE_TRAVEL_MINUTES = 25 # Tiempo de cruce sin congestión (min)
BOOTH_MAX_CORRECTION = 120     # Cap máximo de corrección en minutos (reportes de hasta 4h reales)


def estimate_active_booths(hour, minute):
    """
    Estima la cantidad de casillas abiertas en la aduana argentina
    usando interpolación lineal entre la hora actual y la siguiente.

    Args:
        hour: Hora actual en Argentina (0-23)
        minute: Minuto actual en Argentina (0-59)

    Returns:
        Número estimado de casillas activas (float, 1.0-8.0)
    """
    next_hour = (hour + 1) % 24
    current_booths = BOOTH_SCHEDULE[hour]
    next_booths = BOOTH_SCHEDULE[next_hour]
    fraction = minute / 60.0
    return current_booths + (next_booths - current_booths) * fraction


def calculate_booth_correction(base_minutes, active_booths):
    """
    Calcula minutos adicionales de corrección basándose en la cantidad
    de casillas abiertas vs. la referencia de horario pico.

    Lógica:
    1. Incluso con tránsito fluido, calculamos una espera base en la aduana según casillas.
       Con 2 casillas (mínimo nocturno), la espera base debería rondar los 20 min.
       Con 8 casillas (máximo/referencia), la espera base es 0 min.
       Fórmula: baseline = (BOOTH_REFERENCE - active_booths) * 3.33
       
    2. Si hay congestión real (ej. Waze > 25), la fila avanza más lento,
       por lo que la porción de congestión se multiplica por el factor de casillas:
       congestión_adicional = (base - 25) * (BOOTH_REFERENCE / active_booths - 1.0)
       
    La corrección total es la mayor de las dos (espera base o congestión amplificada).
    """
    # 1. Espera base en aduana según casillas abiertas
    booth_baseline_wait = max(0.0, (BOOTH_REFERENCE - active_booths) * 3.33)

    # 2. Congestión adicional por cuello de botella
    congestion_portion = max(0.0, base_minutes - BOOTH_BASE_TRAVEL_MINUTES)
    if active_booths > 0:
        correction_factor = (BOOTH_REFERENCE / active_booths) - 1.0
    else:
        correction_factor = 0.0
    congestion_adicional = congestion_portion * correction_factor

    # Corrección total
    correction = max(booth_baseline_wait, congestion_adicional)
    return min(round(correction, 1), BOOTH_MAX_CORRECTION)


# Estructura global para almacenar en memoria el último resultado
trafico_cache = {
    "ida_encarnacion": None,
    "vuelta_posadas": None,
    "timestamp": None,
    "status": "initializing",
    "error_message": None
}

# Historial de lecturas base (Waze + visual) de la vuelta para suavizado (últimos 30m)
waze_vuelta_history = []


def site_base_url():
    """URL absoluta del sitio (HTTPS en Render detrás de proxy)."""
    return request.url_root.rstrip('/')


def build_og_meta():
    base = site_base_url()
    cache = trafico_cache
    og_image = f"{base}/og-image.webp"

    # Static title for <title> tag — Google indexes this as the main link text.
    # Must NOT contain dynamic values that become stale between crawls.
    page_title = "Tráfico Puente Posadas-Encarnación en Vivo | PuenteHoy"

    if cache.get("status") == "success":
        ida = cache.get("ida_encarnacion", "--")
        vuelta = cache.get("vuelta_posadas", "--")
        # Dynamic og:title — only used for social sharing previews (WhatsApp,
        # Twitter, etc.) which re-fetch on every share, so values stay fresh.
        og_title = f"Puente en vivo: {ida} ida · {vuelta} vuelta"
        og_description = (
            f"Posadas → Encarnación: {ida}. "
            f"Encarnación → Posadas: {vuelta}. "
            "Clima, tren internacional y cotizaciones."
        )
    else:
        og_title = "Tráfico Puente Posadas-Encarnación en Vivo"
        og_description = (
            "Consultá el tiempo de cruce del Puente San Roque González, "
            "clima y horarios del tren internacional."
        )

    return {
        "page_title": page_title,
        "og_title": og_title,
        "og_description": og_description,
        "og_image": og_image,
        "og_url": f"{base}/",
    }


def extract_minutes_py(s):
    if not s:
        return None
    try:
        return int(str(s).replace('min', ''))
    except (ValueError, TypeError):
        return None


def get_congestion_level_py(m):
    if m is None:
        return {"level": "", "label": "CALCULANDO...", "emoji": "", "key": ""}
    if m <= 45:
        return {"level": "level-agil", "label": "Ágil", "emoji": "🟢", "key": "agil"}
    elif m <= 90:
        return {"level": "level-moderado", "label": "Moderado", "emoji": "🟡", "key": "moderado"}
    elif m <= 120:
        return {"level": "level-cargado", "label": "Cargado", "emoji": "🟠", "key": "cargado"}
    else:
        return {"level": "level-colapsado", "label": "Colapsado", "emoji": "🔴", "key": "colapsado"}


def build_index_context():
    context = build_og_meta()
    cache = trafico_cache
    
    ida_raw = cache.get("ida_encarnacion")
    vuelta_raw = cache.get("vuelta_posadas")
    
    ida_mins = extract_minutes_py(ida_raw)
    vuelta_mins = extract_minutes_py(vuelta_raw)
    
    level_ida = get_congestion_level_py(ida_mins)
    level_vuelta = get_congestion_level_py(vuelta_mins)
    
    context["ida_encarnacion_raw"] = ida_mins
    context["vuelta_posadas_raw"] = vuelta_mins
    context["status_ida"] = level_ida["label"]
    context["status_vuelta"] = level_vuelta["label"]
    context["level_ida_key"] = level_ida["key"]
    context["level_vuelta_key"] = level_vuelta["key"]
    context["level_ida_label"] = level_ida["label"]
    context["show_ads"] = True
    
    if cache.get("status") == "success":
        ticker_text = f"🚗 TRÁNSITO EN VIVO: A ENCARNACIÓN {ida_mins if ida_mins is not None else '--'} MIN - {level_ida['label'].upper()} • A POSADAS {vuelta_mins if vuelta_mins is not None else '--'} MIN - {level_vuelta['label'].upper()}"
    else:
        ticker_text = "OBTENIENDO INFORMACIÓN DE TRÁNSITO..."
        
    context["ticker_text"] = ticker_text
    return context



def update_traffic_data():
    """Función que corre en segundo plano y actualiza el caché continuamente"""
    global trafico_cache

    while True:
        try:
            logging.info("Actualizando caché de Waze...")

            route_ida = WazeRouteCalculator.WazeRouteCalculator(POSADAS_COORDS, ENCARNACION_COORDS, REGION)
            tiempo_ida_raw, _ = route_ida.calc_route_info(real_time=True)

            route_vuelta = WazeRouteCalculator.WazeRouteCalculator(ENCARNACION_COORDS, POSADAS_COORDS, REGION)
            tiempo_vuelta_raw, _ = route_vuelta.calc_route_info(real_time=True)

            tiempo_ida = tiempo_ida_raw
            tiempo_vuelta = tiempo_vuelta_raw

            # Variables de telemetría para persistir en BD
            visual_penalty_ida = 0.0
            visual_penalty_vuelta = 0.0
            active_booths = None
            booth_correction = 0.0
            guard_penalty = 0.0

            # --- Vision Penalty Integration ---
            if db_enabled():
                try:
                    with db_conn() as conn:
                        with conn.cursor(cursor_factory=RealDictCursor) as cur:
                            # Get latest penalty for ida (Posadas -> Encarnacion)
                            cur.execute(
                                "SELECT penalty_minutes FROM bridge_visuals WHERE direction = 'Posadas_to_Encarnacion' ORDER BY recorded_at DESC LIMIT 1;"
                            )
                            res_ida = cur.fetchone()
                            if res_ida:
                                visual_penalty_ida = float(res_ida['penalty_minutes'])
                                tiempo_ida += visual_penalty_ida
                            
                            # Get latest penalty for vuelta (Encarnacion -> Posadas)
                            cur.execute(
                                "SELECT penalty_minutes FROM bridge_visuals WHERE direction = 'Encarnacion_to_Posadas' ORDER BY recorded_at DESC LIMIT 1;"
                            )
                            res_vuelta = cur.fetchone()
                            if res_vuelta:
                                visual_penalty_vuelta = float(res_vuelta['penalty_minutes'])
                                tiempo_vuelta += visual_penalty_vuelta
                except Exception as ex:
                    logging.error("Error al obtener penalizaciones de visión: %s", ex)

            # --- Guardar tiempo base antes de correcciones para suavizado/decaimiento ---
            tiempo_vuelta_base = tiempo_vuelta
            try:
                global waze_vuelta_history
                waze_vuelta_history.append(tiempo_vuelta_base)
                waze_vuelta_history = waze_vuelta_history[-6:]
            except Exception as ex:
                logging.error("Error al actualizar historial de Waze: %s", ex)

            # --- Corrección por casillas abiertas (solo Encarnación → Posadas) ---
            # Debe aplicarse ANTES de la penalización de guardia para que el pipeline sea:
            # Waze base → +visual → ×casillas (con baseline) → +cambio de guardia
            try:
                from zoneinfo import ZoneInfo
                now_ar = datetime.now(ZoneInfo('America/Argentina/Cordoba'))
                active_booths = estimate_active_booths(now_ar.hour, now_ar.minute)
                booth_correction = calculate_booth_correction(tiempo_vuelta, active_booths)
                if booth_correction > 0:
                    tiempo_vuelta += booth_correction
                    logging.info(
                        "Corrección por casillas aplicada: +%.1f min (casillas: %.1f, hora AR: %02d:%02d, base: %.0f min)",
                        booth_correction, active_booths, now_ar.hour, now_ar.minute,
                        tiempo_vuelta - booth_correction,
                    )
            except Exception as ex:
                logging.error("Error al calcular corrección por casillas: %s", ex)

            # --- Penalización por cambio de guardia (solo Encarnación → Posadas) ---
            try:
                from zoneinfo import ZoneInfo
                now_ar = datetime.now(ZoneInfo('America/Argentina/Cordoba'))
                guard_penalty = calculate_guard_shift_penalty(
                    now_ar.hour, now_ar.minute, tiempo_vuelta
                )
                if guard_penalty > 0:
                    tiempo_vuelta += guard_penalty
                    logging.info(
                        "Penalización cambio de guardia aplicada: +%.1f min (hora AR: %02d:%02d, base: %.0f min)",
                        guard_penalty, now_ar.hour, now_ar.minute, tiempo_vuelta - guard_penalty,
                    )
            except Exception as ex:
                logging.error("Error al calcular penalización de cambio de guardia: %s", ex)

            # --- Suavizado / Decaimiento gradual (solo Encarnación → Posadas) ---
            try:
                # Verificar si estuvo fluido los últimos 30 min (6 intervalos de 5 min)
                waze_fully_fluid_30m = (
                    len(waze_vuelta_history) >= 6 and 
                    all(x <= BOOTH_BASE_TRAVEL_MINUTES for x in waze_vuelta_history)
                )
                
                # Definir el target
                if waze_fully_fluid_30m:
                    # Si estuvo verdaderamente fluido por 30m, la estimación real es la de Waze únicamente
                    target = tiempo_vuelta_base
                else:
                    # Si no, mantenemos el cálculo completo con corrección de casillas y guardia
                    target = tiempo_vuelta
                
                # Obtener valor previo de caché para aplicar el decaimiento gradual
                t_prev_str = trafico_cache.get("vuelta_posadas")
                if t_prev_str:
                    try:
                        T_prev = float(t_prev_str.replace("min", "").strip())
                    except ValueError:
                        T_prev = None
                else:
                    T_prev = None
                
                # Aplicar decaimiento si el target es menor que el valor anterior
                MAX_DECREASE_PER_CYCLE = 15.0
                if T_prev is not None:
                    if target < T_prev:
                        tiempo_vuelta = max(target, T_prev - MAX_DECREASE_PER_CYCLE)
                        logging.info(
                            "Decaimiento gradual aplicado: %.1f min -> %.1f min (target: %.1f min, fluido 30m: %s)",
                            T_prev, tiempo_vuelta, target, waze_fully_fluid_30m
                        )
                    else:
                        tiempo_vuelta = target
                else:
                    tiempo_vuelta = target
            except Exception as ex:
                logging.error("Error al aplicar suavizado de tiempo de vuelta: %s", ex)

            trafico_cache["ida_encarnacion"] = f"{tiempo_ida:.0f}min"
            trafico_cache["vuelta_posadas"] = f"{tiempo_vuelta:.0f}min"
            trafico_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
            trafico_cache["status"] = "success"
            trafico_cache["error_message"] = None

            logging.info(
                "Caché actualizado exitosamente: %s / %s",
                trafico_cache["ida_encarnacion"],
                trafico_cache["vuelta_posadas"],
            )

            # --- Persistir lectura histórica con telemetría completa ---
            if db_enabled():
                try:
                    with db_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO traffic_readings (
                                    recorded_at, 
                                    ida_minutes, 
                                    vuelta_minutes,
                                    waze_ida_raw,
                                    waze_vuelta_raw,
                                    visual_penalty_ida,
                                    visual_penalty_vuelta,
                                    active_booths,
                                    booth_correction,
                                    guard_penalty
                                )
                                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s);
                                """,
                                (
                                    round(tiempo_ida, 1),
                                    round(tiempo_vuelta, 1),
                                    round(tiempo_ida_raw, 1) if tiempo_ida_raw is not None else None,
                                    round(tiempo_vuelta_raw, 1) if tiempo_vuelta_raw is not None else None,
                                    round(visual_penalty_ida, 1),
                                    round(visual_penalty_vuelta, 1),
                                    round(active_booths, 1) if active_booths is not None else None,
                                    round(booth_correction, 1) if booth_correction is not None else None,
                                    round(guard_penalty, 1) if guard_penalty is not None else None
                                ),
                            )
                        conn.commit()
                except Exception as ex:
                    logging.error("Error al guardar lectura histórica: %s", ex)

            # --- Procesamiento de Alertas Web Push ---
            try:
                m_ida = int(round(tiempo_ida))
                m_vuelta = int(round(tiempo_vuelta))
                
                subs = load_subscriptions()
                updated_subs = []
                any_change = False
                
                for s in subs:
                    direction = s.get("direction", "ida")
                    threshold = s.get("threshold", 60)
                    last_val = s.get("last_notified_value")

                    if direction == "ambas":
                        if not isinstance(last_val, dict):
                            last_val = {"ida": None, "vuelta": None}

                        next_last = dict(last_val)
                        ok = True
                        for d, current_val, direction_label in (
                            ("ida", m_ida, "Posadas ➔ Encarnación"),
                            ("vuelta", m_vuelta, "Encarnación ➔ Posadas"),
                        ):
                            prev = next_last.get(d)
                            if prev is None:
                                next_last[d] = current_val
                                continue

                            if current_val > threshold and prev <= threshold:
                                title = "⚠️ Alerta de Demora"
                                body = f"El tiempo de cruce en {direction_label} superó los {threshold} min (Actual: {current_val} min)."
                                ok = send_push_notification(s, title, body)
                            elif current_val <= threshold and prev > threshold:
                                title = "✅ Tránsito Fluidificado"
                                body = f"El tiempo de cruce en {direction_label} bajó a {current_val} min (Límite: {threshold} min)."
                                ok = send_push_notification(s, title, body)

                            if not ok:
                                break
                            next_last[d] = current_val

                        if ok:
                            if s.get("last_notified_value") != next_last:
                                s["last_notified_value"] = next_last
                                any_change = True
                            updated_subs.append(s)
                        else:
                            any_change = True  # se descarta
                        continue

                    # Compatibilidad con valores antiguos inesperados.
                    if direction not in ("ida", "vuelta"):
                        direction = "ida"

                    current_val = m_ida if direction == "ida" else m_vuelta
                    direction_label = "Posadas ➔ Encarnación" if direction == "ida" else "Encarnación ➔ Posadas"

                    # Si venía de "ambas", forzamos reinicio para este modo.
                    if isinstance(last_val, dict):
                        last_val = None

                    if last_val is None:
                        s["last_notified_value"] = current_val
                        any_change = True
                        updated_subs.append(s)
                        continue

                    # Tráfico superó el límite (demora)
                    if current_val > threshold and last_val <= threshold:
                        title = "⚠️ Alerta de Demora"
                        body = f"El tiempo de cruce en {direction_label} superó los {threshold} min (Actual: {current_val} min)."
                        success = send_push_notification(s, title, body)
                        if success:
                            s["last_notified_value"] = current_val
                            any_change = True
                            updated_subs.append(s)
                        else:
                            any_change = True  # se descarta

                    # Tráfico bajó del límite (fluido)
                    elif current_val <= threshold and last_val > threshold:
                        title = "✅ Tránsito Fluidificado"
                        body = f"El tiempo de cruce en {direction_label} bajó a {current_val} min (Límite: {threshold} min)."
                        success = send_push_notification(s, title, body)
                        if success:
                            s["last_notified_value"] = current_val
                            any_change = True
                            updated_subs.append(s)
                        else:
                            any_change = True  # se descarta

                    else:
                        if last_val != current_val:
                            s["last_notified_value"] = current_val
                            any_change = True
                        updated_subs.append(s)
                        
                if any_change:
                    save_subscriptions(updated_subs)
            except Exception as ex:
                logging.error("Error al procesar alertas push en loop: %s", ex)

        except Exception as e:
            logging.error("Error al consultar Waze: %s", e)
            trafico_cache["status"] = "error"
            trafico_cache["error_message"] = str(e)

        time.sleep(UPDATE_INTERVAL_SECONDS)


thread_started = False


@app.before_request
def start_background_updater():
    global thread_started
    if not thread_started:
        thread_started = True
        updater_thread = threading.Thread(target=update_traffic_data, daemon=True)
        updater_thread.start()



@app.route('/', methods=['GET'])
def index():
    """Ruta para el microfrontend en smartphone (render.com)"""
    return render_template('index.html', **build_index_context())


@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    base = site_base_url()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{base}/</loc>
        <changefreq>always</changefreq>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>{base}/privacy</loc>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
    </url>
    <url>
        <loc>{base}/terms</loc>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
    </url>
    <url>
        <loc>{base}/contact</loc>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
    </url>
</urlset>"""
    response = app.response_class(xml, mimetype='application/xml')
    return response


@app.route('/robots.txt', methods=['GET'])
def robots():
    base = site_base_url()
    text = f"""User-agent: *
Allow: /

Sitemap: {base}/sitemap.xml"""
    response = app.response_class(text, mimetype='text/plain')
    return response


@app.route('/og-image.webp', methods=['GET'])
def og_image_static():
    """Imagen Open Graph estática (WhatsApp cachea previews agresivamente)."""
    response = send_from_directory('static/images', 'vista_previa.webp', mimetype='image/webp')
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response


@app.route('/api/trafico', methods=['GET'])
def get_trafico():
    """Endpoint principal de la API para devolver los tiempos de viaje"""
    return jsonify(trafico_cache)


@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')


@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

# New Policy Pages
@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')


@app.route('/sw.js')
def service_worker():
    response = app.send_static_file('sw.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Cache-Control'] = 'no-cache'
    return response


# --- Nuevas Rutas de la API de Web Push ---

@app.route('/api/push/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    try:
        pub_key = fetch_vapid_public_key()
        if pub_key:
            return jsonify({"public_key": pub_key})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"error": "VAPID public key not found"}), 404


@app.route('/api/push/subscribe', methods=['POST'])
def subscribe():
    data = request.json
    if not data or 'subscription' not in data or 'threshold' not in data or 'direction' not in data:
        return jsonify({"error": "Missing parameters"}), 400
        
    subscription = data['subscription']
    threshold = int(data['threshold'])
    direction = data['direction']
    
    subs = load_subscriptions()
    endpoint = subscription.get('endpoint')
    existing = next((s for s in subs if s.get('endpoint') == endpoint), None)
    
    if existing:
        existing['threshold'] = threshold
        existing['direction'] = direction
        existing['last_notified_value'] = None
    else:
        subs.append({
            "endpoint": endpoint,
            "keys": subscription.get('keys'),
            "threshold": threshold,
            "direction": direction,
            "last_notified_value": None
        })
        
    save_subscriptions(subs)
    return jsonify({"status": "success"})


@app.route('/api/push/unsubscribe', methods=['POST'])
def unsubscribe():
    data = request.json
    if not data or 'endpoint' not in data:
        return jsonify({"error": "Missing endpoint"}), 400
        
    endpoint = data['endpoint']
    subs = load_subscriptions()
    new_subs = [s for s in subs if s.get('endpoint') != endpoint]
    
    if len(new_subs) != len(subs):
        save_subscriptions(new_subs)
        return jsonify({"status": "success"})
    return jsonify({"status": "not_found"}), 404


@app.route('/api/push/test', methods=['POST'])
def test_push():
    data = request.json or {}
    endpoint = data.get('endpoint')
    
    subs = load_subscriptions()
    if endpoint:
        target_subs = [s for s in subs if s.get('endpoint') == endpoint]
    else:
        target_subs = subs
        
    if not target_subs:
        return jsonify({"error": "No subscription found to test"}), 404
        
    success_count = 0
    for s in target_subs:
        success = send_push_notification(
            s, 
            "🔔 Notificación de Prueba", 
            "¡Hola! Tu sistema de alertas para el Puente Posadas-Encarnación está funcionando correctamente."
        )
        if success:
            success_count += 1
            
    if success_count == 0:
        return jsonify({"error": "No se pudo entregar la notificación. Revisá los logs del servidor."}), 500
    return jsonify({"status": "success", "notified": success_count})


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    app.run(host='0.0.0.0', port=5000)
