"""Genera la imagen Open Graph con los tiempos de tráfico actuales."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC_DIR = Path(__file__).resolve().parent / 'static' / 'images'
FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
]

BG = (15, 23, 42)
TEXT = (255, 255, 255)
MUTED = (148, 163, 184)
ACCENT = (102, 126, 234)
GREEN = (52, 211, 153)
YELLOW = (250, 204, 21)
ORANGE = (251, 146, 60)
RED = (248, 113, 113)


def _load_font(size, bold=False):
    paths = FONT_CANDIDATES if bold else FONT_CANDIDATES[1:]
    if bold:
        paths = [FONT_CANDIDATES[0], FONT_CANDIDATES[2]]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _congestion_label(minutes):
    if minutes is None:
        return 'Sin datos', MUTED
    if minutes <= 45:
        return 'Ágil', GREEN
    if minutes <= 90:
        return 'Moderado', YELLOW
    if minutes <= 120:
        return 'Cargado', ORANGE
    return 'Colapsado', RED


def _parse_minutes(value):
    if not value:
        return None
    try:
        return int(str(value).replace('min', '').strip())
    except ValueError:
        return None


def _draw_bg(img):
    """Fondo oscuro con foto del puente semitransparente si existe."""
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, img.width, img.height), fill=BG)
    bridge_path = STATIC_DIR / 'puente-agil.webp'
    if bridge_path.exists():
        try:
            bridge = Image.open(bridge_path).convert('RGB').resize((img.width, img.height))
            img.paste(bridge)
            overlay = Image.new('RGB', img.size, BG)
            img.paste(overlay, mask=Image.new('L', img.size, 170))
        except OSError:
            pass


def generate_og_jpeg(trafico_cache):
    w, h = 1200, 630
    img = Image.new('RGB', (w, h), BG)
    _draw_bg(img)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(52, bold=True)
    font_sub = _load_font(30)
    font_big = _load_font(64, bold=True)
    font_badge = _load_font(28, bold=True)

    draw.text((56, 48), 'Puente San Roque González', fill=TEXT, font=font_title)
    draw.text((56, 112), 'Posadas ↔ Encarnación · En vivo', fill=MUTED, font=font_sub)

    status = trafico_cache.get('status')
    if status == 'success':
        ida = trafico_cache.get('ida_encarnacion', '--')
        vuelta = trafico_cache.get('vuelta_posadas', '--')
        m_ida = _parse_minutes(ida)
        m_vuelta = _parse_minutes(vuelta)
        worst = max(m for m in (m_ida, m_vuelta) if m is not None) if any(
            m is not None for m in (m_ida, m_vuelta)
        ) else None
        label, color = _congestion_label(worst)

        draw.text((56, 200), 'Posadas → Encarnación', fill=MUTED, font=font_sub)
        draw.text((56, 245), ida, fill=TEXT, font=font_big)
        draw.text((620, 200), 'Encarnación → Posadas', fill=MUTED, font=font_sub)
        draw.text((620, 245), vuelta, fill=TEXT, font=font_big)

        badge_text = f'Estado: {label}'
        bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
        bw, bh = bbox[2] - bbox[0] + 40, bbox[3] - bbox[1] + 24
        bx, by = 56, 480
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=20, fill=(30, 41, 59), outline=color, width=3)
        draw.text((bx + 20, by + 8), badge_text, fill=color, font=font_badge)
    elif status == 'initializing':
        draw.text((56, 260), 'Calculando tiempos de cruce…', fill=ACCENT, font=font_big)
    else:
        draw.text((56, 260), 'Tráfico no disponible', fill=RED, font=font_sub)

    draw.text((56, 560), 'trafico-puente-san-roque-waze.onrender.com', fill=MUTED, font=font_sub)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88, optimize=True)
    buf.seek(0)
    return buf
