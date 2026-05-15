from flask import Flask, jsonify, render_template
import WazeRouteCalculator
import threading
import time
import logging
from datetime import datetime, timezone

# Desactivar logs innecesarios de la librería de Waze
logging.getLogger("WazeRouteCalculator.WazeRouteCalculator").setLevel(logging.WARNING)

app = Flask(__name__)

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

def update_traffic_data():
    """Función que corre en segundo plano y actualiza el caché continuamente"""
    global trafico_cache
    
    while True:
        try:
            logging.info("Actualizando caché de Waze...")
            
            # Consultar Posadas -> Encarnación
            route_ida = WazeRouteCalculator.WazeRouteCalculator(POSADAS_COORDS, ENCARNACION_COORDS, REGION)
            tiempo_ida_raw, _ = route_ida.calc_route_info(real_time=True)
            
            # Consultar Encarnación -> Posadas
            route_vuelta = WazeRouteCalculator.WazeRouteCalculator(ENCARNACION_COORDS, POSADAS_COORDS, REGION)
            tiempo_vuelta_raw, _ = route_vuelta.calc_route_info(real_time=True)
            
            # Ajuste del 25% extra (Waze suele subestimar vs Google Maps)
            tiempo_ida = tiempo_ida_raw * 1.25
            tiempo_vuelta = tiempo_vuelta_raw * 1.25
            
            # Actualizar Caché Global
            trafico_cache["ida_encarnacion"] = f"{tiempo_ida:.0f}min"
            trafico_cache["vuelta_posadas"] = f"{tiempo_vuelta:.0f}min"
            trafico_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
            trafico_cache["status"] = "success"
            trafico_cache["error_message"] = None
            
            logging.info(f"Caché actualizado exitosamente: {trafico_cache['ida_encarnacion']} / {trafico_cache['vuelta_posadas']}")
            
        except Exception as e:
            logging.error(f"Error al consultar Waze: {e}")
            trafico_cache["status"] = "error"
            trafico_cache["error_message"] = str(e)
            
        # Dormir el hilo durante el intervalo configurado
        time.sleep(UPDATE_INTERVAL_SECONDS)

thread_started = False

@app.before_request
def start_background_updater():
    global thread_started
    if not thread_started:
        thread_started = True
        # Iniciar el hilo en segundo plano
        updater_thread = threading.Thread(target=update_traffic_data, daemon=True)
        updater_thread.start()

@app.route('/', methods=['GET'])
def index():
    """Ruta para el microfrontend en smartphone (render.com)"""
    return render_template('index.html')

@app.route('/api/trafico', methods=['GET'])
def get_trafico():
    """Endpoint principal de la API para devolver los tiempos de viaje"""
    return jsonify(trafico_cache)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    # Ejecutar la app Flask
    app.run(host='0.0.0.0', port=5000)
