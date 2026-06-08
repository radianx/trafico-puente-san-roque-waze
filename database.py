import os
import json
import logging
import time
from contextlib import contextmanager
import config

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError:
    psycopg2 = None
    Json = None
    RealDictCursor = None

DB_RUNTIME_DISABLED = False
DB_RUNTIME_DISABLE_REASON = None
DB_LAST_FAILURE_TIME = 0
DB_CIRCUIT_BREAKER_COOLDOWN = 300  # 5 minutos en segundos


def get_db_url():
    return os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")


def db_enabled():
    global DB_RUNTIME_DISABLED
    if not get_db_url() or psycopg2 is None:
        return False
    if DB_RUNTIME_DISABLED:
        # Si ya pasó el tiempo de cooldown, permitimos intentar conectarse de nuevo
        if time.time() - DB_LAST_FAILURE_TIME > DB_CIRCUIT_BREAKER_COOLDOWN:
            logging.info("Reintentando conexión a Base de Datos (Circuit Breaker Cooldown finalizado)...")
            DB_RUNTIME_DISABLED = False
        else:
            return False
    return True


@contextmanager
def db_conn():
    global DB_RUNTIME_DISABLED, DB_RUNTIME_DISABLE_REASON, DB_LAST_FAILURE_TIME
    url = get_db_url()
    if not url:
        raise RuntimeError("Supabase/Postgres URL is not configured")
    try:
        with psycopg2.connect(url) as conn:
            yield conn
    except Exception as exc:
        DB_RUNTIME_DISABLED = True
        DB_RUNTIME_DISABLE_REASON = str(exc)
        DB_LAST_FAILURE_TIME = time.time()
        logging.error("Fallo de conexión a la Base de Datos. Se activa circuit breaker. Error: %s", exc)
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
        DB_LAST_FAILURE_TIME = time.time()
        logging.warning(
            "No se pudo conectar a Supabase/Postgres durante la inicialización. Se activa fallback local. error=%s",
            exc,
        )


def migrate_local_files_to_db_if_needed():
    if not db_enabled():
        return
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT 1 FROM push_config WHERE config_key='vapid';")
                has_cfg = bool(cur.fetchone())
                cur.execute("SELECT 1 FROM push_subscriptions LIMIT 1;")
                has_subs = bool(cur.fetchone())
    except Exception as exc:
        logging.warning("Error al verificar estado de tablas en migración: %s", exc)
        return

    if has_cfg and has_subs:
        return

    migrated_any = False
    if not has_cfg and os.path.exists(config.PRIVATE_KEY_FILE) and os.path.exists(config.PUBLIC_KEY_FILE):
        try:
            with open(config.PRIVATE_KEY_FILE, "rb") as f:
                pem = f.read().decode("utf-8")
            with open(config.PUBLIC_KEY_FILE, "r") as f:
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
        except Exception as exc:
            logging.error("Error al migrar claves VAPID a la BD: %s", exc)

    if not has_subs and os.path.exists(config.SUBSCRIPTIONS_FILE):
        try:
            try:
                with open(config.SUBSCRIPTIONS_FILE, "r") as f:
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
                                    Json(s.get("last_notified_value")) if Json is not None else json.dumps(s.get("last_notified_value")),
                                ),
                            )
                    conn.commit()
                migrated_any = True
        except Exception as exc:
            logging.error("Error al migrar suscripciones a la BD: %s", exc)

    if migrated_any:
        logging.info("Migración local->Supabase completada para push_config/push_subscriptions.")
