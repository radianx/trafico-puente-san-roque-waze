from flask import Flask, jsonify, render_template, request, send_from_directory
import WazeRouteCalculator
import threading
import time
import logging
from datetime import datetime, timezone
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
    if db_enabled():
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT private_key_pem FROM push_config WHERE config_key='vapid';")
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("VAPID private key not found in DB")
                return row["private_key_pem"]
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

# Configuración de Coordenadas y Región
POSADAS_COORDS = "-27.4035,-55.8928"
ENCARNACION_COORDS = "-27.3522,-55.8588"
REGION = 'EU'
UPDATE_INTERVAL_SECONDS = 5 * 60  # 5 minutos

# Estructura global para almacenar en memoria el último resultado
trafico_cache = {
    "ida_encarnacion": None,
    "vuelta_posadas": None,
    "timestamp": None,
    "status": "initializing",
    "error_message": None
}


def site_base_url():
    """URL absoluta del sitio (HTTPS en Render detrás de proxy)."""
    return request.url_root.rstrip('/')


def build_og_meta():
    base = site_base_url()
    cache = trafico_cache
    og_image = f"{base}/og-image.webp"

    if cache.get("status") == "success":
        ida = cache.get("ida_encarnacion", "--")
        vuelta = cache.get("vuelta_posadas", "--")
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
        "og_title": og_title,
        "og_description": og_description,
        "og_image": og_image,
        "og_url": f"{base}/",
    }


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

            # --- Procesamiento de Alertas Web Push ---
            # KNOWN BUG: Las notificaciones reales no están llegando aunque /api/push/test
            # funciona. Investigar:
            #   1. get_vapid_private_key_for_webpush() puede retornar el path del archivo
            #      PEM en local/fallback, que pywebpush acepta como ruta. Verificar que en
            #      producción (Render) la DB esté activa y la clave se obtenga como string.
            #   2. DB_RUNTIME_DISABLED puede activarse en el hilo de background sin contexto
            #      Flask, haciendo que load_subscriptions() devuelva [].
            #   3. last_notified_value=None en el primer ciclo hace skip implícito —
            #      revisar si esto impide el primer disparo cuando el umbral ya está superado.
            # TODO (Perfiles): Mover la lógica de suscripciones a perfiles de usuario
            # cuando se implemente autenticación. Cada perfil tendrá su propio endpoint,
            # umbral y dirección preferida.
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
    return render_template('index.html', **build_og_meta())


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


@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')


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
