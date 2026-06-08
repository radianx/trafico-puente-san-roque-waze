from flask import Flask, jsonify, render_template, request, send_from_directory
import logging
from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix

# Importar módulos locales
from database import init_db, migrate_local_files_to_db_if_needed
from push import (
    init_vapid,
    fetch_vapid_public_key,
    load_subscriptions,
    save_subscriptions,
    send_push_notification
)
from traffic import (
    trafico_cache,
    build_index_context,
    site_base_url,
    start_background_updater
)

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Desactivar logs innecesarios de la librería de Waze
logging.getLogger("WazeRouteCalculator.WazeRouteCalculator").setLevel(logging.WARNING)

# Inicializar Flask
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


@app.context_processor
def inject_globals():
    """Hace disponible current_year en todos los templates."""
    return {"current_year": datetime.now().year}


# Inicializar recursos de Base de Datos y Web Push
init_db()
migrate_local_files_to_db_if_needed()
init_vapid()


@app.before_request
def start_updater():
    """Inicia el hilo secundario encargado de actualizar el tráfico en segundo plano."""
    start_background_updater()


# --- Rutas Web ---

@app.route('/', methods=['GET'])
def index():
    """Ruta para la página de inicio (Smartphone UI)."""
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


@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')


@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')


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


# --- Rutas de la API ---

@app.route('/api/trafico', methods=['GET'])
def get_trafico():
    """Endpoint principal de la API para devolver los tiempos de viaje."""
    return jsonify(trafico_cache)


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
    app.run(host='0.0.0.0', port=5000)
