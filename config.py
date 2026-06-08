import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# --- Archivos locales de fallback ---
PRIVATE_KEY_FILE = "private_key.pem"
PUBLIC_KEY_FILE = "public_key.txt"
SUBSCRIPTIONS_FILE = "subscriptions.json"

# --- Configuración de Coordenadas y Región ---
POSADAS_COORDS = "-27.4035,-55.8928"
ENCARNACION_COORDS = "-27.3522,-55.8588"
REGION = 'EU'
UPDATE_INTERVAL_SECONDS = 5 * 60  # 5 minutos

# --- Penalización por cambio de guardia en Aduana Argentina ---
GUARD_SHIFT_HOURS = [7, 15, 23]  # Horas de cambio de guardia (America/Argentina/Cordoba)
GUARD_SHIFT_MAX_PENALTY = 60     # Penalización máxima en minutos al momento exacto del cambio
GUARD_SHIFT_DECAY_MINUTES = 45   # Ventana en minutos donde la penalización decae a cero
GUARD_SHIFT_CONGESTION_THRESHOLD = 40  # Solo aplicar si la estimación base ya supera este umbral

# --- Estimación heurística de casillas abiertas en Aduana Argentina ---
BOOTH_SCHEDULE = {
    0: 1.5,  1: 1.5,  2: 1.5,  3: 1.5,  4: 1.5,  5: 2,
    6: 3,    7: 4,    8: 5,    9: 5.5,  10: 6,   11: 6,
    12: 6,   13: 6,   14: 6,   15: 6,   16: 6.5, 17: 6.5,
    18: 6,   19: 5,   20: 4,   21: 3,   22: 2.5, 23: 2,
}
BOOTH_REFERENCE = 8            # Casillas en horario pico. Los sensores están "calibrados" a este nivel.
BOOTH_BASE_TRAVEL_MINUTES = 25 # Tiempo de cruce sin congestión (min)
BOOTH_MAX_CORRECTION = 120     # Cap máximo de corrección en minutos (reportes de hasta 4h reales)
