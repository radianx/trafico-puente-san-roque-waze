// === Constants ===
const WMO = {
    0:{d:'Despejado',i:'☀️'},1:{d:'Mayormente despejado',i:'🌤️'},2:{d:'Parcialmente nublado',i:'⛅'},
    3:{d:'Nublado',i:'☁️'},45:{d:'Niebla',i:'🌫️'},48:{d:'Niebla helada',i:'🌫️'},
    51:{d:'Llovizna leve',i:'🌦️'},53:{d:'Llovizna',i:'🌦️'},55:{d:'Llovizna intensa',i:'🌧️'},
    61:{d:'Lluvia leve',i:'🌦️'},63:{d:'Lluvia',i:'🌧️'},65:{d:'Lluvia intensa',i:'🌧️'},
    71:{d:'Nevada leve',i:'🌨️'},73:{d:'Nevada',i:'🌨️'},75:{d:'Nevada intensa',i:'❄️'},
    80:{d:'Chaparrones',i:'🌦️'},81:{d:'Chaparrones',i:'🌧️'},82:{d:'Chaparrones fuertes',i:'🌧️'},
    95:{d:'Tormenta',i:'⛈️'},96:{d:'Tormenta con granizo',i:'⛈️'},99:{d:'Tormenta con granizo',i:'⛈️'}
};
const DAY_NAMES = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
const STALE_MINUTES = 15;
const NORMAL_RANGE = '25–45 min';
const TAB_LABELS = { trafico: 'Tráfico', clima: 'Clima', info: 'Info' };
const congestionOrder = ['agil','moderado','cargado','colapsado'];
const CURRENCY_PREFIX = { USD: 'U$D', ARS: 'AR$', PYG: 'PY₲' };
const BRIDGE_ALTS = {
    agil: 'Puente con tráfico ágil',
    moderado: 'Puente con tráfico moderado',
    cargado: 'Puente con tráfico cargado',
    colapsado: 'Puente con tráfico colapsado'
};

let prevMIda = null;
let prevMVuelta = null;
let lastTrafficData = null;
let bridgeImagesLoaded = new Set();

// === Toast ===
function showToast(message, type = 'info', duration = 4500) {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), duration);
}

// === Tabs (ARIA + keyboard) ===
const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));
const tabPanes = Array.from(document.querySelectorAll('.tab-pane'));

function activateTab(btn) {
    const tabId = btn.dataset.tab;
    tabButtons.forEach((b) => {
        const selected = b === btn;
        b.classList.toggle('active', selected);
        b.setAttribute('aria-selected', selected ? 'true' : 'false');
        b.tabIndex = selected ? 0 : -1;
    });
    tabPanes.forEach((p) => {
        const active = p.id === 'tab-' + tabId;
        p.classList.toggle('active', active);
        p.hidden = !active;
    });
    document.getElementById('tab-announcer').textContent = `Mostrando: ${TAB_LABELS[tabId]}`;
}

tabButtons.forEach((btn, i) => {
    btn.addEventListener('click', () => activateTab(btn));
    btn.addEventListener('keydown', (e) => {
        let next = -1;
        if (e.key === 'ArrowRight') next = (i + 1) % tabButtons.length;
        else if (e.key === 'ArrowLeft') next = (i - 1 + tabButtons.length) % tabButtons.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabButtons.length - 1;
        if (next >= 0) {
            e.preventDefault();
            tabButtons[next].focus();
            activateTab(tabButtons[next]);
        }
    });
});

// === Clock & train ===
function updateClockAndTrain() {
    const now = new Date();
    const fmtAR = new Intl.DateTimeFormat('es-AR', { timeZone: 'America/Argentina/Cordoba', hour: '2-digit', minute: '2-digit', hour12: false });
    const fmtPY = new Intl.DateTimeFormat('es-PY', { timeZone: 'America/Asuncion', hour: '2-digit', minute: '2-digit', hour12: false });
    const arTime = fmtAR.format(now);
    const pyTime = fmtPY.format(now);
    document.getElementById('clock-ar-text').textContent = arTime;
    document.getElementById('clock-py-text').textContent = pyTime;

    const arDateParts = new Intl.DateTimeFormat('en', {
        timeZone: 'America/Argentina/Cordoba', hour: 'numeric', minute: 'numeric', weekday: 'short', hour12: false
    }).formatToParts(now);
    let weekday = '', hour = 0, minute = 0;
    arDateParts.forEach((p) => {
        if (p.type === 'weekday') weekday = p.value;
        if (p.type === 'hour') hour = parseInt(p.value, 10);
        if (p.type === 'minute') minute = parseInt(p.value, 10);
    });

    const trainDot = document.getElementById('train-dot');
    const trainStatus = document.getElementById('train-status');
    const isWeekend = ['Sat', 'Sun'].includes(weekday);
    const currentMins = hour * 60 + minute;
    const trainStart = 7 * 60;
    const trainEnd = 18 * 60 + 30;

    if (isWeekend) {
        trainDot.className = 'qb-dot off';
        trainStatus.textContent = 'No opera hoy';
    } else if (currentMins >= trainStart && currentMins <= trainEnd) {
        trainDot.className = 'qb-dot on';
        trainStatus.textContent = 'En servicio';
    } else {
        trainDot.className = 'qb-dot off';
        trainStatus.textContent = 'Fuera de horario';
    }
}
updateClockAndTrain();
setInterval(updateClockAndTrain, 30000);

// === Traffic helpers ===
function formatRelativeTime(isoString) {
    if (!isoString) return '--';
    const diffMins = Math.floor((Date.now() - new Date(isoString).getTime()) / 60000);
    if (diffMins < 1) return 'hace instantes';
    if (diffMins === 1) return 'hace 1 min';
    return `hace ${diffMins} min`;
}

function minutesSince(isoString) {
    if (!isoString) return Infinity;
    return Math.floor((Date.now() - new Date(isoString).getTime()) / 60000);
}

function extractMinutes(s) {
    return s ? parseInt(String(s).replace('min', ''), 10) : null;
}

function getCongestionLevel(m) {
    if (m === null || isNaN(m)) return { level: '', label: '', emoji: '', key: '' };
    if (m <= 45) return { level: 'level-agil', label: 'Ágil', emoji: '🟢', key: 'agil' };
    if (m <= 90) return { level: 'level-moderado', label: 'Moderado', emoji: '🟡', key: 'moderado' };
    if (m <= 120) return { level: 'level-cargado', label: 'Cargado', emoji: '🟠', key: 'cargado' };
    return { level: 'level-colapsado', label: 'Colapsado', emoji: '🔴', key: 'colapsado' };
}

function applyCongestion(cardId, badgeId, minutes) {
    const card = document.getElementById(cardId);
    const badge = document.getElementById(badgeId);
    const { level, label, emoji } = getCongestionLevel(minutes);
    card.className = 'route-card' + (level ? ' ' + level : '');
    badge.className = 'congestion-badge' + (level ? ' ' + level : '');
    badge.textContent = level ? `${emoji} ${label}` : '';
}

function setRouteHint(hintId, minutes, prev) {
    const el = document.getElementById(hintId);
    let hint = `Referencia habitual: ${NORMAL_RANGE}`;
    el.className = 'route-hint';
    if (prev !== null && prev !== undefined && minutes !== null && !isNaN(minutes) && prev !== minutes) {
        const delta = minutes - prev;
        const sign = delta > 0 ? '+' : '';
        hint += ` · ${sign}${delta} min desde la última lectura`;
        el.classList.add(delta > 0 ? 'delta-up' : 'delta-down');
    }
    el.textContent = hint;
}

function updateTrafficSummary(mI, mV) {
    const summary = document.getElementById('traffic-summary');
    if (mI === null || mV === null || isNaN(mI) || isNaN(mV)) {
        summary.hidden = true;
        return;
    }
    summary.hidden = false;
    document.getElementById('ts-ida').textContent = `Ida ${mI} min`;
    document.getElementById('ts-vuelta').textContent = `Vuelta ${mV} min`;

    const worstInfo = getCongestionLevel(Math.max(mI, mV));
    const worstEl = document.getElementById('ts-worst');
    worstEl.textContent = `Estado: ${worstInfo.label}`;
    worstEl.className = 'ts-worst ' + worstInfo.level;
}

function updateBridgeImage(mIda, mVuelta) {
    const idxI = congestionOrder.indexOf(getCongestionLevel(mIda).key);
    const idxV = congestionOrder.indexOf(getCongestionLevel(mVuelta).key);
    const worst = idxI >= idxV ? getCongestionLevel(mIda).key : getCongestionLevel(mVuelta).key;
    if (!worst) return;

    const sk = document.getElementById('bridge-skeleton');
    const target = document.getElementById('img-' + worst);
    const showActive = () => {
        if (sk) sk.style.display = 'none';
        document.querySelectorAll('.bridge-img').forEach((img) => img.classList.remove('active'));
        if (target) target.classList.add('active');
    };

    if (target && !target.getAttribute('src') && target.dataset.src) {
        target.onload = showActive;
        target.src = target.dataset.src;
        target.alt = BRIDGE_ALTS[worst] || 'Vista del puente';
        bridgeImagesLoaded.add(worst);
    } else {
        showActive();
    }

    document.getElementById('bridge-viewer').className = 'bridge-viewer glow-' + worst;
    const info = getCongestionLevel(Math.max(mIda, mVuelta));
    document.getElementById('bridge-caption').textContent =
        `Tráfico ${info.label.toLowerCase()} · Imagen representativa`;
}

function renderTrafficSuccess(data) {
    const mI = extractMinutes(data.ida_encarnacion);
    const mV = extractMinutes(data.vuelta_posadas);

    document.getElementById('time-ida').innerHTML = `${mI}<span>min</span>`;
    document.getElementById('time-vuelta').innerHTML = `${mV}<span>min</span>`;
    setRouteHint('hint-ida', mI, prevMIda);
    setRouteHint('hint-vuelta', mV, prevMVuelta);

    applyCongestion('card-ida', 'badge-ida', mI);
    applyCongestion('card-vuelta', 'badge-vuelta', mV);
    updateBridgeImage(mI, mV);
    updateTrafficSummary(mI, mV);

    prevMIda = mI;
    prevMVuelta = mV;
    lastTrafficData = data;
}

function setStaleWarning(isoString) {
    const staleEl = document.getElementById('stale-warning');
    staleEl.hidden = minutesSince(isoString) < STALE_MINUTES;
}

function setTrafficUIState({ dotClass, statusText, lastUpdateText, showRetry, errorMsg }) {
    document.getElementById('status-dot').className = 'status-dot ' + dotClass;
    document.getElementById('status-text').textContent = statusText;
    document.getElementById('last-update').textContent = lastUpdateText || '--';
    document.getElementById('btn-retry').hidden = !showRetry;

    const errorBox = document.getElementById('error-box');
    if (errorMsg) {
        errorBox.textContent = errorMsg;
        errorBox.style.display = 'block';
    } else {
        errorBox.style.display = 'none';
        errorBox.textContent = '';
    }
}

async function fetchTrafficData(manual = false) {
    const refreshBtn = document.getElementById('btn-refresh');
    if (manual) {
        refreshBtn.disabled = true;
        refreshBtn.classList.add('is-spinning');
    }

    try {
        const res = await fetch('/api/trafico');
        if (!res.ok) throw new Error('No pudimos conectar con el servidor. Comprobá tu red e intentá de nuevo.');

        const data = await res.json();

        if (data.status === 'initializing') {
            setTrafficUIState({
                dotClass: 'initializing',
                statusText: 'Calculando...',
                lastUpdateText: 'Esperando primer dato...',
                showRetry: false
            });
            document.getElementById('stale-warning').hidden = true;
        } else if (data.status === 'success') {
            renderTrafficSuccess(data);
            setTrafficUIState({
                dotClass: 'success',
                statusText: 'En vivo',
                lastUpdateText: 'Actualizado: ' + formatRelativeTime(data.timestamp),
                showRetry: false
            });
            setStaleWarning(data.timestamp);
            if (manual) showToast('Tráfico actualizado', 'success', 2500);
        } else {
            setTrafficUIState({
                dotClass: 'error',
                statusText: 'Error',
                lastUpdateText: data.timestamp ? 'Actualizado: ' + formatRelativeTime(data.timestamp) : '--',
                showRetry: true,
                errorMsg: data.error_message || 'No se pudo obtener la información del puente.'
            });
            showToast('Error al obtener tráfico', 'error');
        }
    } catch (e) {
        console.error('Fetch error:', e);
        setTrafficUIState({
            dotClass: 'error',
            statusText: 'Desconectado',
            lastUpdateText: lastTrafficData?.timestamp
                ? 'Último dato: ' + formatRelativeTime(lastTrafficData.timestamp)
                : '--',
            showRetry: true,
            errorMsg: e.message || 'No pudimos conectar con el servidor. Comprobá tu red e intentá de nuevo.'
        });
        showToast('Sin conexión al servidor', 'error');
    } finally {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove('is-spinning');
    }
}

document.getElementById('btn-refresh').addEventListener('click', () => fetchTrafficData(true));
document.getElementById('btn-retry').addEventListener('click', () => fetchTrafficData(true));

fetchTrafficData();
setInterval(() => fetchTrafficData(), 30000);

// === Share ===
function buildShareText() {
    if (!lastTrafficData || lastTrafficData.status !== 'success') {
        return 'Puente San Roque González (Posadas ↔ Encarnación) — Consultá el tráfico en vivo.';
    }
    const mI = extractMinutes(lastTrafficData.ida_encarnacion);
    const mV = extractMinutes(lastTrafficData.vuelta_posadas);
    const rel = formatRelativeTime(lastTrafficData.timestamp);
    return `🌉 Puente Posadas ↔ Encarnación\n` +
        `Posadas → Encarnación: ${mI} min\n` +
        `Encarnación → Posadas: ${mV} min\n` +
        `Actualizado ${rel}\n` +
        window.location.href;
}

async function shareStatus() {
    const text = buildShareText();
    if (navigator.share) {
        try {
            await navigator.share({ title: 'Tráfico Puente', text, url: window.location.href });
            return;
        } catch (e) {
            if (e.name === 'AbortError') return;
        }
    }
    const waUrl = 'https://wa.me/?text=' + encodeURIComponent(text);
    window.open(waUrl, '_blank', 'noopener,noreferrer');
}

document.getElementById('btn-share').addEventListener('click', shareStatus);

// === Weather ===
function setWeatherLoading(loading) {
    const block = document.getElementById('weather-current');
    block.classList.toggle('is-loading', loading);
    if (!loading) {
        document.getElementById('weather-loaded').hidden = false;
    }
}

async function fetchWeather(manual = false) {
    setWeatherLoading(true);
    const skRow = document.getElementById('forecast-skeleton');
    if (skRow) skRow.style.display = 'flex';

    try {
        const url = 'https://api.open-meteo.com/v1/forecast?latitude=-27.36&longitude=-55.90'
            + '&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m'
            + '&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'
            + '&timezone=America/Argentina/Cordoba&forecast_days=7';
        const res = await fetch(url);
        if (!res.ok) throw new Error('Respuesta inválida');
        const data = await res.json();

        const c = data.current;
        const wmo = WMO[c.weather_code] || { d: 'Desconocido', i: '❓' };
        document.getElementById('w-icon').textContent = wmo.i;
        document.getElementById('w-temp').textContent = Math.round(c.temperature_2m) + '°';
        document.getElementById('w-desc').textContent = wmo.d;
        document.getElementById('w-feels').textContent = Math.round(c.apparent_temperature) + '°';
        document.getElementById('w-humidity').textContent = c.relative_humidity_2m + '%';
        document.getElementById('w-wind').textContent = Math.round(c.wind_speed_10m) + ' km/h';
        setWeatherLoading(false);

        const grid = document.getElementById('forecast-grid');
        grid.innerHTML = '';
        const d = data.daily;
        for (let i = 0; i < d.time.length; i++) {
            const date = new Date(d.time[i] + 'T12:00:00');
            const dayName = i === 0 ? 'Hoy' : DAY_NAMES[date.getDay()];
            const wmoDay = WMO[d.weather_code[i]] || { d: '', i: '❓' };
            const rain = d.precipitation_probability_max[i];
            const card = document.createElement('div');
            card.className = 'forecast-card' + (i === 0 ? ' fc-today' : '');
            card.innerHTML = `
                <div class="fc-day">${dayName}</div>
                <div class="fc-icon" aria-hidden="true">${wmoDay.i}</div>
                <div class="fc-temps tabular-nums">${Math.round(d.temperature_2m_max[i])}° <span class="fc-min">${Math.round(d.temperature_2m_min[i])}°</span></div>
                ${rain > 10 ? `<div class="fc-rain">💧 ${rain}%</div>` : ''}
            `;
            grid.appendChild(card);
        }
        if (manual) showToast('Clima actualizado', 'success', 2500);
    } catch (e) {
        console.error('Weather error:', e);
        setWeatherLoading(false);
        document.getElementById('w-desc').textContent = 'Error al cargar clima';
        showToast('No se pudo cargar el clima. Intentá más tarde.', 'error');
        const grid = document.getElementById('forecast-grid');
        if (grid && !grid.querySelector('.forecast-card')) {
            grid.innerHTML = '<p class="schedule-note" style="padding:0.5rem">Sin pronóstico disponible.</p>';
        }
    }
}
fetchWeather();
setInterval(fetchWeather, 30 * 60 * 1000);

// === Currency ===
const convRates = { USD: 1, ARS: 1, PYG: 1 };
let ratesUpdatedAt = null;

function updateCurrencyPrefixes() {
    const from = document.getElementById('conv-from').value;
    const to = document.getElementById('conv-to').value;
    document.getElementById('conv-amount-prefix').textContent = CURRENCY_PREFIX[from];
    document.getElementById('conv-result-prefix').textContent = CURRENCY_PREFIX[to];
}

function setConvLoading(loading) {
    const result = document.getElementById('conv-result');
    result.classList.toggle('is-loading', loading);
    if (loading) {
        document.getElementById('conv-result-value').textContent = '';
    }
}

async function fetchRates() {
    setConvLoading(true);
    document.getElementById('conv-retry').hidden = true;

    try {
        const [dolarRes, exchangeRes] = await Promise.all([
            fetch('https://dolarapi.com/v1/dolares/blue'),
            fetch('https://api.exchangerate-api.com/v4/latest/USD')
        ]);
        if (!dolarRes.ok || !exchangeRes.ok) throw new Error('API error');

        const dolarData = await dolarRes.json();
        const exchangeData = await exchangeRes.json();

        convRates.ARS = dolarData.venta;
        convRates.PYG = exchangeData.rates.PYG;
        ratesUpdatedAt = new Date();

        const timeStr = ratesUpdatedAt.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
        document.getElementById('conv-rates').innerHTML =
            `1 USD = $${convRates.ARS} ARS · 1 USD = ₲${Math.round(convRates.PYG).toLocaleString('es-AR')} PYG` +
            `<br><span class="conv-meta">Cotización al ${timeStr}. Ref: Dólar Blue (AR) y divisa intl. (PY).</span>`;

        document.getElementById('conv-result').classList.remove('is-loading');
        updateConversion();
    } catch (e) {
        console.error('Rates error:', e);
        document.getElementById('conv-rates').textContent =
            'No se pudieron obtener las cotizaciones.';
        document.getElementById('conv-result').classList.remove('is-loading');
        document.getElementById('conv-result-value').textContent = '--';
        document.getElementById('conv-retry').hidden = false;
        showToast('Error al cargar cotizaciones', 'error');
    }
}

function updateConversion() {
    updateCurrencyPrefixes();
    if (convRates.ARS === 1) return;

    const amount = parseFloat(document.getElementById('conv-amount').value) || 0;
    const from = document.getElementById('conv-from').value;
    const to = document.getElementById('conv-to').value;

    const amountInUSD = amount / convRates[from];
    const result = amountInUSD * convRates[to];

    let formattedResult = '';
    if (to === 'USD') formattedResult = result.toFixed(2);
    else if (to === 'ARS') formattedResult = Math.round(result).toLocaleString('es-AR');
    else formattedResult = Math.round(result).toLocaleString('es-AR');

    document.getElementById('conv-result').classList.remove('is-loading');
    document.getElementById('conv-result-value').textContent = formattedResult;
}

document.getElementById('conv-amount').addEventListener('input', updateConversion);
document.getElementById('conv-from').addEventListener('change', updateConversion);
document.getElementById('conv-to').addEventListener('change', updateConversion);
updateCurrencyPrefixes();
document.getElementById('conv-retry').addEventListener('click', fetchRates);

document.getElementById('conv-swap').addEventListener('click', () => {
    const from = document.getElementById('conv-from');
    const to = document.getElementById('conv-to');
    const tmp = from.value;
    from.value = to.value;
    to.value = tmp;
    updateConversion();
});

fetchRates();
setInterval(fetchRates, 60 * 60 * 1000);

// === PWA service worker ===
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
}
