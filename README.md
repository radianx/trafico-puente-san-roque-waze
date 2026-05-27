# API Tráfico Puente (Posadas - Encarnación) 🚗🌉

Una API ligera construida en Python y Flask que obtiene los tiempos de cruce del puente San Roque González de Santa Cruz (entre Posadas, Argentina y Encarnación, Paraguay) consultando internamente los servidores de Waze.

## ¿Cómo funciona?

Waze cuenta con fuertes protecciones antibot. Para evitar ser bloqueados y al mismo tiempo proveer respuestas instantáneas a nuestro Widget o App, esta API usa la siguiente arquitectura:
1. **Background Threading:** Al iniciarse, un hilo invisible (`daemon thread`) se despierta cada 5 minutos.
2. **Scraping Silencioso:** Usa la librería [WazeRouteCalculator](https://github.com/kovacsvalentin/WazeRouteCalculator) para negociar tokens y consultar las rutas actualizadas de Ida y Vuelta.
3. **Caché Global:** Guarda los resultados en la memoria RAM del servidor.
4. **Respuesta Flash:** Cuando un usuario hace una petición `GET` a `/api/trafico`, el servidor simplemente le devuelve el último JSON cacheado en cuestión de milisegundos.

## Requisitos

- Python 3.8 o superior
- Pip (Gestor de paquetes de Python)

## Instalación y Uso Local

1. **Clonar el repositorio y entrar en la carpeta:**
   ```bash
   git clone https://github.com/tu-usuario/trafico-puente-api.git
   cd trafico-puente-api
   ```

2. **(Recomendado) Crear un entorno virtual:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Correr el servidor:**
   ```bash
   python app.py
   ```
   El servidor arrancará en `http://localhost:5000`.

## Endpoint de la API

### `GET /api/trafico`

**Respuesta Exitosa (Ejemplo):**
```json
{
  "ida_encarnacion": "26min",
  "vuelta_posadas": "34min",
  "timestamp": "2026-05-14T15:26:37.987333",
  "status": "success",
  "error_message": null
}
```

## Despliegue (Deploy Gratuito)

Este proyecto está listo para ser desplegado en plataformas como **Render.com** o **Railway.app**:
1. Conecta tu cuenta de GitHub a la plataforma elegida.
2. Selecciona este repositorio.
3. La plataforma detectará automáticamente `requirements.txt` y correrá el servidor Flask sin configuraciones extra.

### Persistencia de Push con Supabase (recomendado en Render)

Para que las notificaciones push no se rompan en reinicios/deploys, configurá una base PostgreSQL (Supabase):

1. En tu proyecto de Supabase, copiá la URL de conexión Postgres.
2. En Render, agregá una variable de entorno:
   - `SUPABASE_DB_URL` (o `DATABASE_URL`) con la URL completa.
3. Redeploy del servicio.

Con eso, la app crea automáticamente estas tablas:
- `push_config` (claves VAPID)
- `push_subscriptions` (suscripciones del navegador)

Si no hay URL configurada, la app sigue en modo local usando archivos (`private_key.pem`, `public_key.txt`, `subscriptions.json`).

## Tecnologías
- [Flask](https://flask.palletsprojects.com/) (Framework web)
- [WazeRouteCalculator](https://github.com/kovacsvalentin/WazeRouteCalculator) (Engine para Waze)

---
*Disclaimer: Esta API consume datos no oficiales de Waze a través de ingeniería inversa de la comunidad. Puede romperse si Waze cambia su esquema de seguridad interno.*
