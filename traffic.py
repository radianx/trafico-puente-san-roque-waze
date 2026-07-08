import math
import logging
import threading
import time
from datetime import datetime, timezone
from flask import request
import WazeRouteCalculator

from concurrent.futures import ThreadPoolExecutor
import config
from database import db_enabled, db_conn, RealDictCursor
from push import (
    load_subscriptions,
    save_subscriptions,
    send_push_notification,
    update_subscription_last_notified,
    unsubscribe_endpoint
)

# Cache global de tráfico
trafico_cache = {
    "ida_encarnacion": None,
    "vuelta_posadas": None,
    "timestamp": None,
    "status": "initializing",
    "error_message": None
}

# Historial de lecturas base (Waze + visual) de la vuelta para suavizado (últimos 30m)
waze_vuelta_history = []


def calculate_guard_shift_penalty(now_ar_hour, now_ar_minute, base_estimate_minutes):
    """
    Calcula la penalización por cambio de guardia en la Aduana Argentina.
    """
    if base_estimate_minutes <= config.GUARD_SHIFT_CONGESTION_THRESHOLD:
        return 0.0

    current_total_minutes = now_ar_hour * 60 + now_ar_minute

    min_elapsed = None
    for shift_hour in config.GUARD_SHIFT_HOURS:
        shift_total = shift_hour * 60
        elapsed = (current_total_minutes - shift_total) % (24 * 60)
        if elapsed < config.GUARD_SHIFT_DECAY_MINUTES:
            if min_elapsed is None or elapsed < min_elapsed:
                min_elapsed = elapsed

    if min_elapsed is None:
        return 0.0

    decay_ratio = (1.0 + math.cos(math.pi * min_elapsed / config.GUARD_SHIFT_DECAY_MINUTES)) / 2.0
    penalty = config.GUARD_SHIFT_MAX_PENALTY * decay_ratio

    return round(penalty, 1)


def estimate_active_booths(hour, minute):
    """
    Estima la cantidad de casillas abiertas en la aduana argentina.
    """
    next_hour = (hour + 1) % 24
    current_booths = config.BOOTH_SCHEDULE[hour]
    next_booths = config.BOOTH_SCHEDULE[next_hour]
    fraction = minute / 60.0
    return current_booths + (next_booths - current_booths) * fraction


def calculate_booth_correction(base_minutes, active_booths):
    """
    Calcula minutos adicionales de corrección basándose en la cantidad de casillas.
    """
    booth_baseline_wait = max(0.0, (config.BOOTH_REFERENCE - active_booths) * 3.33)
    congestion_portion = max(0.0, base_minutes - config.BOOTH_BASE_TRAVEL_MINUTES)
    if active_booths > 0:
        correction_factor = (config.BOOTH_REFERENCE / active_booths) - 1.0
    else:
        correction_factor = 0.0
    congestion_adicional = congestion_portion * correction_factor

    correction = max(booth_baseline_wait, congestion_adicional)
    return min(round(correction, 1), config.BOOTH_MAX_CORRECTION)


def site_base_url():
    """URL absoluta del sitio (HTTPS en Render detrás de proxy)."""
    return request.url_root.rstrip('/')


def build_og_meta():
    base = site_base_url()
    cache = trafico_cache
    og_image = f"{base}/og-image.webp"
    page_title = "Tráfico Puente Posadas-Encarnación en Vivo | PuenteHoy"

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
    context["level_vuelta_label"] = level_vuelta["label"]
    context["show_ads"] = True
    
    if cache.get("status") == "success":
        ticker_text = f"🚗 TRÁNSITO EN VIVO: A ENCARNACIÓN {ida_mins if ida_mins is not None else '--'} MIN - {level_ida['label'].upper()} • A POSADAS {vuelta_mins if vuelta_mins is not None else '--'} MIN - {level_vuelta['label'].upper()}"
    else:
        ticker_text = "OBTENIENDO INFORMACIÓN DE TRÁNSITO..."
        
    context["ticker_text"] = ticker_text
    return context


def update_traffic_data():
    """Función que corre en segundo plano y actualiza el caché continuamente"""
    global trafico_cache, waze_vuelta_history

    while True:
        try:
            logging.info("Actualizando caché de Waze...")

            def get_route_time(start, end):
                route = WazeRouteCalculator.WazeRouteCalculator(start, end, config.REGION)
                t, _ = route.calc_route_info(real_time=True)
                return t

            with ThreadPoolExecutor(max_workers=2) as executor:
                f_ida = executor.submit(get_route_time, config.POSADAS_COORDS, config.ENCARNACION_COORDS)
                f_vuelta = executor.submit(get_route_time, config.ENCARNACION_COORDS, config.POSADAS_COORDS)
                tiempo_ida_raw = f_ida.result()
                tiempo_vuelta_raw = f_vuelta.result()

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
                            cur.execute(
                                "SELECT penalty_minutes FROM bridge_visuals WHERE direction = 'Posadas_to_Encarnacion' ORDER BY recorded_at DESC LIMIT 1;"
                            )
                            res_ida = cur.fetchone()
                            if res_ida:
                                visual_penalty_ida = float(res_ida['penalty_minutes'])
                                tiempo_ida += visual_penalty_ida
                            
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
                waze_vuelta_history.append(tiempo_vuelta_base)
                waze_vuelta_history = waze_vuelta_history[-6:]
            except Exception as ex:
                logging.error("Error al actualizar historial de Waze: %s", ex)

            # --- Corrección por casillas abiertas (solo Encarnación → Posadas) ---
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
                waze_fully_fluid_30m = (
                    len(waze_vuelta_history) >= 6 and 
                    all(x <= config.BOOTH_BASE_TRAVEL_MINUTES for x in waze_vuelta_history)
                )
                
                if waze_fully_fluid_30m:
                    target = tiempo_vuelta_base
                else:
                    target = tiempo_vuelta
                
                t_prev_str = trafico_cache.get("vuelta_posadas")
                if t_prev_str:
                    try:
                        T_prev = float(t_prev_str.replace("min", "").strip())
                    except ValueError:
                        T_prev = None
                else:
                    T_prev = None
                
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
                
                for s in subs:
                    endpoint = s.get("endpoint")
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
                                update_subscription_last_notified(endpoint, next_last)
                        else:
                            unsubscribe_endpoint(endpoint)
                        continue

                    if direction not in ("ida", "vuelta"):
                        direction = "ida"

                    current_val = m_ida if direction == "ida" else m_vuelta
                    direction_label = "Posadas ➔ Encarnación" if direction == "ida" else "Encarnación ➔ Posadas"

                    if isinstance(last_val, dict):
                        last_val = None

                    if last_val is None:
                        s["last_notified_value"] = current_val
                        update_subscription_last_notified(endpoint, current_val)
                        continue

                    if current_val > threshold and last_val <= threshold:
                        title = "⚠️ Alerta de Demora"
                        body = f"El tiempo de cruce en {direction_label} superó los {threshold} min (Actual: {current_val} min)."
                        success = send_push_notification(s, title, body)
                        if success:
                            s["last_notified_value"] = current_val
                            update_subscription_last_notified(endpoint, current_val)
                        else:
                            unsubscribe_endpoint(endpoint)

                    elif current_val <= threshold and last_val > threshold:
                        title = "✅ Tránsito Fluidificado"
                        body = f"El tiempo de cruce en {direction_label} bajó a {current_val} min (Límite: {threshold} min)."
                        success = send_push_notification(s, title, body)
                        if success:
                            s["last_notified_value"] = current_val
                            update_subscription_last_notified(endpoint, current_val)
                        else:
                            unsubscribe_endpoint(endpoint)
            except Exception as ex:
                logging.error("Error al procesar alertas push en loop: %s", ex)

        except Exception as e:
            logging.error("Error al consultar Waze: %s", e)
            trafico_cache["status"] = "error"
            trafico_cache["error_message"] = str(e)

        time.sleep(config.UPDATE_INTERVAL_SECONDS)


thread_started = False


def _preload_waze_history():
    """
    Precarga las últimas 6 lecturas de waze_vuelta_raw desde Supabase
    para que el algoritmo de suavizado tenga contexto histórico
    inmediatamente después de un deploy/reinicio.
    """
    global waze_vuelta_history
    if not db_enabled():
        return
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT waze_vuelta_raw
                    FROM traffic_readings
                    WHERE waze_vuelta_raw IS NOT NULL
                    ORDER BY recorded_at DESC
                    LIMIT 6;
                """)
                rows = cur.fetchall()
        if rows:
            # Los resultados vienen en orden DESC, los invertimos para orden cronológico
            waze_vuelta_history = [float(r[0]) for r in reversed(rows)]
            logging.info(
                "Historial de Waze precargado desde BD: %d lecturas %s",
                len(waze_vuelta_history), waze_vuelta_history
            )
    except Exception as ex:
        logging.warning("No se pudo precargar historial de Waze desde BD: %s", ex)


def start_background_updater():
    global thread_started
    if not thread_started:
        thread_started = True
        _preload_waze_history()
        updater_thread = threading.Thread(target=update_traffic_data, daemon=True)
        updater_thread.start()
