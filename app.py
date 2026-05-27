from flask import Flask, jsonify, render_template, request, send_from_directory
import WazeRouteCalculator
import threading
import time
import logging
from datetime import datetime, timezone

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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    app.run(host='0.0.0.0', port=5000)
