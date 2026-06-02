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
const TAB_LABELS = { trafico: 'Tráfico', alertas: 'Alertas', clima: 'Clima', info: 'Info' };
const congestionOrder = ['agil','moderado','cargado','colapsado'];
const CURRENCY_PREFIX = { USD: 'U$D', ARS: 'AR$', PYG: 'PY₲' };
const BRIDGE_ALTS = {
    agil: 'Puente con tráfico ágil',
    moderado: 'Puente con tráfico moderado',
    cargado: 'Puente con tráfico cargado',
    colapsado: 'Puente con tráfico colapsado'
};
const BRIDGE_IMAGE_URLS = {
    agil: '/static/images/puente-agil.webp',
    moderado: '/static/images/puente-moderado.webp',
    cargado: '/static/images/puente-cargado.webp',
    colapsado: '/static/images/puente-colapsado.webp'
};

let prevMIda = null;
let prevMVuelta = null;
let lastTrafficData = null;
let bridgeImagesLoaded = new Set();

// === Ticker Data Cache ===
const tickerData = {
    traffic: 'CARGANDO ESTADO DEL TRÁNSITO...',
    train: 'CARGANDO ESTADO DEL TREN...',
    weather: 'CARGANDO CLIMA...',
    rates: 'CARGANDO COTIZACIONES...'
};

function renderTicker() {
    const el = document.getElementById('board-ticker-text');
    if (!el) return;
    const parts = [
        tickerData.traffic,
        tickerData.train,
        tickerData.weather,
        tickerData.rates
    ];
    const separator = '   •   ';
    const msg = parts.filter(Boolean).join(separator).toUpperCase();
    el.textContent = msg + separator + msg;
}

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

    // Hide or show the traffic summary depending on active tab
    const summary = document.getElementById('traffic-summary');
    if (summary) {
        if (tabId === 'trafico') {
            summary.hidden = true;
        } else {
            if (lastTrafficData) {
                const mI = extractMinutes(lastTrafficData.ida_encarnacion);
                const mV = extractMinutes(lastTrafficData.vuelta_posadas);
                summary.hidden = (mI === null || mV === null || isNaN(mI) || isNaN(mV));
            } else {
                summary.hidden = true;
            }
        }
    }
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
    
    // Update digital board clocks
    const arHourEl = document.getElementById('board-clock-ar-hour');
    const arMinEl = document.getElementById('board-clock-ar-min');
    const arParts = arTime.split(':');
    if (arHourEl && arMinEl && arParts.length === 2) {
        arHourEl.textContent = arParts[0];
        arMinEl.textContent = arParts[1];
    }
    
    const pyHourEl = document.getElementById('board-clock-py-hour');
    const pyMinEl = document.getElementById('board-clock-py-min');
    const pyParts = pyTime.split(':');
    if (pyHourEl && pyMinEl && pyParts.length === 2) {
        pyHourEl.textContent = pyParts[0];
        pyMinEl.textContent = pyParts[1];
    }

    // Format and update retro date (e.g. LUN 01 JUN)
    const dateFmt = new Intl.DateTimeFormat('es-AR', {
        timeZone: 'America/Argentina/Cordoba',
        weekday: 'short',
        day: '2-digit',
        month: 'short'
    });
    const dateStr = dateFmt.format(now).replace(',', '').toUpperCase();
    const dateEl = document.getElementById('board-date-text');
    if (dateEl) {
        dateEl.textContent = dateStr;
    }

    const arDateParts = new Intl.DateTimeFormat('en', {
        timeZone: 'America/Argentina/Cordoba', hour: 'numeric', minute: 'numeric', weekday: 'short', hour12: false
    }).formatToParts(now);
    let weekday = '', hour = 0, minute = 0;
    arDateParts.forEach((p) => {
        if (p.type === 'weekday') weekday = p.value;
        if (p.type === 'hour') hour = parseInt(p.value, 10);
        if (p.type === 'minute') minute = parseInt(p.value, 10);
    });

    const isWeekend = ['Sat', 'Sun'].includes(weekday);
    const currentMins = hour * 60 + minute;
    const trainStart = 7 * 60;
    const trainEnd = 18 * 60 + 30;

    let trainMsg = '';
    if (isWeekend) {
        trainMsg = 'TREN: NO OPERA HOY';
    } else if (currentMins >= trainStart && currentMins <= trainEnd) {
        trainMsg = 'TREN: EN SERVICIO (07:15 A 18:30)';
    } else {
        trainMsg = 'TREN: FUERA DE HORARIO';
    }
    
    tickerData.train = trainMsg;
    renderTicker();
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
    
    // Hide summary if we are on the 'trafico' tab
    const activeBtn = document.querySelector('.tab-btn.active');
    const isTrafico = activeBtn ? activeBtn.dataset.tab === 'trafico' : true;
    summary.hidden = isTrafico;
    
    const tsIda = document.getElementById('ts-ida');
    const tsVuelta = document.getElementById('ts-vuelta');
    const levelIda = getCongestionLevel(mI);
    const levelVuelta = getCongestionLevel(mV);

    // The digital-board lanes use direction labels "A Encarnación" / "A Posadas"
    if (tsIda) {
        tsIda.textContent = `A Encarnación ${mI} min`;
        tsIda.className = 'ts-item' + (levelIda.level ? ' ' + levelIda.level : '');
    }
    if (tsVuelta) {
        tsVuelta.textContent = `A Posadas ${mV} min`;
        tsVuelta.className = 'ts-item' + (levelVuelta.level ? ' ' + levelVuelta.level : '');
    }

    const worstInfo = getCongestionLevel(Math.max(mI, mV));
    const worstEl = document.getElementById('ts-worst');
    worstEl.textContent = `Estado: ${worstInfo.label}`;
    worstEl.className = 'ts-worst ' + worstInfo.level;
}

function setLaneBackground(laneEl, levelKey) {
    if (!laneEl || !levelKey) return;
    const imageUrl = BRIDGE_IMAGE_URLS[levelKey];
    if (!imageUrl) return;

    laneEl.style.setProperty('--lane-bg-image', `url("${imageUrl}")`);
    laneEl.dataset.bgAlt = BRIDGE_ALTS[levelKey] || 'Vista del puente';

    if (bridgeImagesLoaded.has(levelKey)) return;
    const preloadImg = new Image();
    preloadImg.src = imageUrl;
    bridgeImagesLoaded.add(levelKey);
}

function updateBridgeImage(mIda, mVuelta) {
    const levelIda = getCongestionLevel(mIda);
    const levelVuelta = getCongestionLevel(mVuelta);
    setLaneBackground(document.getElementById('board-lane-ida'), levelIda.key);
    setLaneBackground(document.getElementById('board-lane-vuelta'), levelVuelta.key);
}

function renderTrafficSuccess(data) {
    const mI = extractMinutes(data.ida_encarnacion);
    const mV = extractMinutes(data.vuelta_posadas);

    // Update wait times in the digital board
    const valIda = document.getElementById('board-time-ida');
    const valVuelta = document.getElementById('board-time-vuelta');
    if (valIda) valIda.textContent = (mI !== null && !isNaN(mI)) ? mI : '--';
    if (valVuelta) valVuelta.textContent = (mV !== null && !isNaN(mV)) ? mV : '--';

    // Update congestion levels on lanes
    const laneIda = document.getElementById('board-lane-ida');
    const laneVuelta = document.getElementById('board-lane-vuelta');
    const levelIda = getCongestionLevel(mI);
    const levelVuelta = getCongestionLevel(mV);

    if (laneIda) laneIda.className = 'board-lane' + (levelIda.level ? ' ' + levelIda.level : '');
    if (laneVuelta) laneVuelta.className = 'board-lane' + (levelVuelta.level ? ' ' + levelVuelta.level : '');

    // Update lane statuses
    const statusIda = document.getElementById('board-status-ida');
    const statusVuelta = document.getElementById('board-status-vuelta');
    if (statusIda) statusIda.textContent = levelIda.label || 'DESCONOCIDO';
    if (statusVuelta) statusVuelta.textContent = levelVuelta.label || 'DESCONOCIDO';

    updateBridgeImage(mI, mV);
    updateTrafficSummary(mI, mV);

    // Format changes/deltas for the scrolling ticker tape
    let deltaIda = '';
    if (prevMIda !== null && mI !== null && prevMIda !== mI) {
        const diff = mI - prevMIda;
        deltaIda = ` (${diff > 0 ? '+' : ''}${diff} MIN)`;
    }
    let deltaVuelta = '';
    if (prevMVuelta !== null && mV !== null && prevMVuelta !== mV) {
        const diff = mV - prevMVuelta;
        deltaVuelta = ` (${diff > 0 ? '+' : ''}${diff} MIN)`;
    }

    tickerData.traffic = `🚗 TRÁNSITO EN VIVO: A ENCARNACIÓN ${mI !== null ? mI : '--'} MIN - ${levelIda.label || 'S/D'}${deltaIda} • A POSADAS ${mV !== null ? mV : '--'} MIN - ${levelVuelta.label || 'S/D'}${deltaVuelta}`;
    renderTicker();

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

// === Share (solo URL; preview con imagen vía Open Graph en el servidor) ===
function getShareUrl() {
    return window.location.origin + window.location.pathname;
}

async function shareStatus() {
    const url = getShareUrl();
    if (navigator.share) {
        try {
            await navigator.share({ url });
            return;
        } catch (e) {
            if (e.name === 'AbortError') return;
        }
    }
    window.open('https://wa.me/?text=' + encodeURIComponent(url), '_blank', 'noopener,noreferrer');
}

document.getElementById('btn-share').addEventListener('click', shareStatus);
document.getElementById('btn-share-board').addEventListener('click', shareStatus);

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
        
        tickerData.weather = `🌦️ CLIMA: ${Math.round(c.temperature_2m)}°C (${wmo.d})`;
        renderTicker();
        
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
        tickerData.weather = '🌦️ CLIMA: S/D';
        renderTicker();
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

        tickerData.rates = `💵 COTIZACIONES: 1 USD = $${convRates.ARS} ARS · ₲${Math.round(convRates.PYG).toLocaleString('es-AR')} PYG`;
        renderTicker();

        document.getElementById('conv-result').classList.remove('is-loading');
        updateConversion();
    } catch (e) {
        console.error('Rates error:', e);
        document.getElementById('conv-rates').textContent =
            'No se pudieron obtener las cotizaciones.';
        tickerData.rates = '💵 COTIZACIONES: S/D';
        renderTicker();
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

// === PWA service worker — register early so SW is ready before push logic ===
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
        console.warn('Service Worker registration failed:', err);
    });
}

// === Web Push Notifications Logic ===
//
// KNOWN BUG: El endpoint /api/push/test funciona correctamente (notificación de prueba
// llega al dispositivo), pero las notificaciones reales que se disparan desde el loop
// de tráfico en app.py NO están llegando. Posibles causas a investigar:
//   1. La función get_vapid_private_key_for_webpush() devuelve el path del archivo PEM
//      cuando DB está desactivada, pero pywebpush puede estar interpretándolo distinto
//      entre el endpoint /test (que llama a send_push_notification directamente) y el
//      hilo de background (que corre sin contexto Flask).
//   2. Verificar que DB_RUNTIME_DISABLED no se active silenciosamente en producción,
//      haciendo que load_subscriptions() devuelva lista vacía en el hilo background.
//   3. Revisar que last_notified_value se esté inicializando correctamente y no bloquee
//      el primer disparo de la alerta.
//
// TODO (Perfiles): Cuando se implemente el sistema de perfiles, mover la configuración
// de alertas (dirección, umbral, endpoint push) al perfil del usuario en lugar de
// localStorage anónimo + DB sin identidad. Ver también el comentario en index.html.

let isSubscribed = false;
let activeSubscription = null;

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

function initPushNotifications() {
    const alertSettings = document.getElementById('alert-settings');
    const alertToggle = document.getElementById('alert-toggle');
    const alertDirection = document.getElementById('alert-direction');
    const alertThreshold = document.getElementById('alert-threshold');
    const alertSaveBtn = document.getElementById('alert-save-btn');

    if (!alertSettings) return;

    const unsupportedNote = document.getElementById('alert-unsupported-note');

    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        // Web Push no soportado en este navegador — ocultar el formulario y mostrar nota
        alertSettings.style.display = 'none';
        if (unsupportedNote) unsupportedNote.hidden = false;
        return;
    }

    // Inicializar valores desde localStorage si existen
    if (localStorage.getItem('alert-direction')) {
        alertDirection.value = localStorage.getItem('alert-direction');
    }
    if (localStorage.getItem('alert-threshold')) {
        alertThreshold.value = localStorage.getItem('alert-threshold');
    }

    // Verificar si ya existe suscripción activa
    navigator.serviceWorker.ready.then(reg => {
        return reg.pushManager.getSubscription();
    }).then(subscription => {
        if (subscription) {
            isSubscribed = true;
            activeSubscription = subscription;
            alertToggle.checked = true;
        }
    }).catch(err => {
        console.error('Error al obtener suscripción de push activa:', err);
    });

    async function subscribeUser() {
        try {
            // Solicitar permiso
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') {
                showToast('Permiso de notificaciones denegado.', 'error');
                alertToggle.checked = false;
                return;
            }

            // Obtener llave VAPID pública del backend
            const keyRes = await fetch('/api/push/vapid-public-key');
            const keyData = await keyRes.json();
            if (!keyData.public_key) {
                throw new Error(keyData.error || 'No se obtuvo la llave VAPID');
            }

            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(keyData.public_key)
            });

            // Enviar al servidor
            const threshold = parseInt(alertThreshold.value, 10);
            const direction = alertDirection.value;

            // toJSON() is required to correctly serialize p256dh + auth keys
            const saveRes = await fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subscription: sub.toJSON(),
                    threshold: threshold,
                    direction: direction
                })
            });

            const saveData = await saveRes.json();
            if (saveData.status === 'success') {
                isSubscribed = true;
                activeSubscription = sub;
                localStorage.setItem('alert-direction', direction);
                localStorage.setItem('alert-threshold', threshold);
                showToast('🔔 Alertas activadas con éxito.', 'success');
            } else {
                throw new Error(saveData.error || 'Error al guardar suscripción');
            }
        } catch (err) {
            console.error('Error al suscribir usuario a push:', err);
            showToast('No se pudieron activar las alertas.', 'error');
            alertToggle.checked = false;
        }
    }

    async function unsubscribeUser() {
        if (!activeSubscription) return;
        try {
            // Intentar remover del servidor primero
            await fetch('/api/push/unsubscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ endpoint: activeSubscription.endpoint })
            });

            await activeSubscription.unsubscribe();

            isSubscribed = false;
            activeSubscription = null;
            showToast('🔕 Alertas desactivadas.', 'info');
        } catch (err) {
            console.error('Error al desuscribir usuario:', err);
            showToast('Error al desactivar las alertas.', 'error');
        }
    }

    alertToggle.addEventListener('change', () => {
        if (alertToggle.checked) {
            subscribeUser();
        } else {
            unsubscribeUser();
        }
    });

    alertSaveBtn.addEventListener('click', async () => {
        if (!isSubscribed || !activeSubscription) return;

        alertSaveBtn.disabled = true;
        alertSaveBtn.textContent = 'Guardando...';

        try {
            const threshold = parseInt(alertThreshold.value, 10);
            const direction = alertDirection.value;

            const saveRes = await fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subscription: activeSubscription,
                    threshold: threshold,
                    direction: direction
                })
            });

            const saveData = await saveRes.json();
            if (saveData.status === 'success') {
                localStorage.setItem('alert-direction', direction);
                localStorage.setItem('alert-threshold', threshold);
                showToast('💾 Configuración guardada.', 'success');
            } else {
                throw new Error(saveData.error || 'Error al guardar');
            }
        } catch (err) {
            console.error('Error al guardar configuración:', err);
            showToast('No se pudo guardar la configuración.', 'error');
        } finally {
            alertSaveBtn.disabled = false;
            alertSaveBtn.textContent = 'Guardar Configuración';
        }
    });

}

initPushNotifications();

// (service worker already registered above, before push init)
