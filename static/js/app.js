// === WMO Weather Code Mapping ===
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

// === Tab Switching ===
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-selected','false'); });
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        btn.setAttribute('aria-selected','true');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
});

// === Dual Clock & Train Status ===
function updateClockAndTrain() {
    const now = new Date();
    const fmtAR = new Intl.DateTimeFormat('es-AR',{timeZone:'America/Argentina/Cordoba',hour:'2-digit',minute:'2-digit',hour12:false});
    const fmtPY = new Intl.DateTimeFormat('es-PY',{timeZone:'America/Asuncion',hour:'2-digit',minute:'2-digit',hour12:false});
    const arTime = fmtAR.format(now);
    const pyTime = fmtPY.format(now);

    const clockText = document.getElementById('clock-text');
    if (arTime === pyTime) {
        clockText.textContent = arTime + ' (misma hora)';
    } else {
        clockText.textContent = '🇦🇷 ' + arTime + ' · 🇵🇾 ' + pyTime;
    }

    // Train status
    const arDateParts = new Intl.DateTimeFormat('en',{timeZone:'America/Argentina/Cordoba',hour:'numeric',minute:'numeric',weekday:'short',hour12:false}).formatToParts(now);
    let weekday = '', hour = 0, minute = 0;
    arDateParts.forEach(p => {
        if (p.type === 'weekday') weekday = p.value;
        if (p.type === 'hour') hour = parseInt(p.value);
        if (p.type === 'minute') minute = parseInt(p.value);
    });

    const trainDot = document.getElementById('train-dot');
    const trainStatus = document.getElementById('train-status');
    const isWeekend = ['Sat','Sun'].includes(weekday);
    const currentMins = hour * 60 + minute;
    const trainStart = 7 * 60;       // 07:00
    const trainEnd = 18 * 60 + 30;   // 18:30

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

// === Traffic ===
function formatRelativeTime(isoString) {
    if (!isoString) return '--:--';
    const diffMins = Math.floor((new Date() - new Date(isoString)) / 60000);
    if (diffMins < 1) return 'hace instantes';
    if (diffMins === 1) return 'hace 1 min';
    return `hace ${diffMins} min`;
}

function extractMinutes(s) { return s ? parseInt(s.replace('min',''),10) : null; }

const congestionOrder = ['agil','moderado','cargado','colapsado'];
function getCongestionLevel(m) {
    if (m === null || isNaN(m)) return {level:'',label:'',emoji:'',key:''};
    if (m <= 45) return {level:'level-agil',label:'Ágil',emoji:'🟢',key:'agil'};
    if (m <= 90) return {level:'level-moderado',label:'Moderado',emoji:'🟡',key:'moderado'};
    if (m <= 120) return {level:'level-cargado',label:'Cargado',emoji:'🟠',key:'cargado'};
    return {level:'level-colapsado',label:'Colapsado',emoji:'🔴',key:'colapsado'};
}

function applyCongestion(cardId, badgeId, minutes) {
    const card = document.getElementById(cardId);
    const badge = document.getElementById(badgeId);
    const {level,label,emoji} = getCongestionLevel(minutes);
    card.className = 'route-card' + (level ? ' '+level : '');
    badge.className = 'congestion-badge' + (level ? ' '+level : '');
    badge.textContent = level ? `${emoji} ${label}` : '';
}

function updateBridgeImage(mIda, mVuelta) {
    const idxI = congestionOrder.indexOf(getCongestionLevel(mIda).key);
    const idxV = congestionOrder.indexOf(getCongestionLevel(mVuelta).key);
    const worst = idxI >= idxV ? getCongestionLevel(mIda).key : getCongestionLevel(mVuelta).key;
    if (!worst) return;
    const sk = document.getElementById('bridge-skeleton');
    if (sk) sk.style.display = 'none';
    document.querySelectorAll('.bridge-img').forEach(img => img.classList.remove('active'));
    const t = document.getElementById('img-'+worst);
    if (t) t.classList.add('active');
    document.getElementById('bridge-viewer').className = 'bridge-viewer glow-'+worst;
}

async function fetchTrafficData() {
    try {
        const res = await fetch('/api/trafico');
        if (!res.ok) throw new Error('Error en la red');
        const data = await res.json();
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        const lastUpdate = document.getElementById('last-update');
        const errorBox = document.getElementById('error-box');

        if (data.status === 'initializing') {
            statusDot.className = 'status-dot initializing';
            statusText.textContent = 'Calculando...';
        } else if (data.status === 'success') {
            statusDot.className = 'status-dot success';
            statusText.textContent = 'En vivo';
            const mI = extractMinutes(data.ida_encarnacion);
            const mV = extractMinutes(data.vuelta_posadas);
            document.getElementById('time-ida').innerHTML = `${mI}<span>min</span>`;
            document.getElementById('time-vuelta').innerHTML = `${mV}<span>min</span>`;
            lastUpdate.textContent = 'Actualizado: ' + formatRelativeTime(data.timestamp);
            errorBox.style.display = 'none';
            applyCongestion('card-ida','badge-ida',mI);
            applyCongestion('card-vuelta','badge-vuelta',mV);
            updateBridgeImage(mI,mV);
        } else {
            statusDot.className = 'status-dot error';
            statusText.textContent = 'Error';
            errorBox.textContent = data.error_message || 'No se pudo obtener la información.';
            errorBox.style.display = 'block';
        }
    } catch(e) {
        console.error('Fetch error:',e);
        document.getElementById('status-dot').className = 'status-dot error';
        document.getElementById('status-text').textContent = 'Desconectado';
    }
}
fetchTrafficData();
setInterval(fetchTrafficData, 30000);

// === Weather (Open-Meteo, called from frontend) ===
async function fetchWeather() {
    try {
        const url = 'https://api.open-meteo.com/v1/forecast?latitude=-27.36&longitude=-55.90'
            + '&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m'
            + '&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'
            + '&timezone=America/Argentina/Cordoba&forecast_days=7';
        const res = await fetch(url);
        const data = await res.json();

        // Current weather
        const c = data.current;
        const wmo = WMO[c.weather_code] || {d:'Desconocido',i:'❓'};
        document.getElementById('w-icon').textContent = wmo.i;
        document.getElementById('w-temp').textContent = Math.round(c.temperature_2m) + '°';
        document.getElementById('w-desc').textContent = wmo.d;
        document.getElementById('w-feels').textContent = Math.round(c.apparent_temperature) + '°';
        document.getElementById('w-humidity').textContent = c.relative_humidity_2m + '%';
        document.getElementById('w-wind').textContent = Math.round(c.wind_speed_10m) + ' km/h';

        // 7-day forecast
        const grid = document.getElementById('forecast-grid');
        grid.innerHTML = '';
        const d = data.daily;
        for (let i = 0; i < d.time.length; i++) {
            const date = new Date(d.time[i] + 'T12:00:00');
            const dayName = i === 0 ? 'Hoy' : DAY_NAMES[date.getDay()];
            const wmoDay = WMO[d.weather_code[i]] || {d:'',i:'❓'};
            const rain = d.precipitation_probability_max[i];
            const card = document.createElement('div');
            card.className = 'forecast-card' + (i === 0 ? ' fc-today' : '');
            card.innerHTML = `
                <div class="fc-day">${dayName}</div>
                <div class="fc-icon">${wmoDay.i}</div>
                <div class="fc-temps">${Math.round(d.temperature_2m_max[i])}° <span class="fc-min">${Math.round(d.temperature_2m_min[i])}°</span></div>
                ${rain > 10 ? `<div class="fc-rain">💧 ${rain}%</div>` : ''}
            `;
            grid.appendChild(card);
        }
    } catch(e) {
        console.error('Weather error:',e);
        document.getElementById('w-desc').textContent = 'Error al cargar clima';
    }
}
fetchWeather();
setInterval(fetchWeather, 30 * 60 * 1000); // Refresh every 30 min

// === Currency Converter ===
const convRates = { USD: 1, ARS: 1, PYG: 1 };
async function fetchRates() {
    try {
        const [dolarRes, exchangeRes] = await Promise.all([
            fetch('https://dolarapi.com/v1/dolares/blue'),
            fetch('https://api.exchangerate-api.com/v4/latest/USD')
        ]);
        const dolarData = await dolarRes.json();
        const exchangeData = await exchangeRes.json();

        convRates.ARS = dolarData.venta;
        convRates.PYG = exchangeData.rates.PYG;

        document.getElementById('conv-rates').innerHTML = `💵 1 USD = $${convRates.ARS} ARS | 1 USD = ₲${Math.round(convRates.PYG)} PYG<br>Ref: Dólar Blue (AR) y divisa intl. (PY).`;
        updateConversion();
    } catch(e) {
        console.error('Rates error:', e);
        document.getElementById('conv-rates').textContent = 'Error al obtener cotizaciones.';
    }
}

function updateConversion() {
    if (convRates.ARS === 1) return; // not loaded yet
    const amount = parseFloat(document.getElementById('conv-amount').value) || 0;
    const from = document.getElementById('conv-from').value;
    const to = document.getElementById('conv-to').value;

    const amountInUSD = amount / convRates[from];
    const result = amountInUSD * convRates[to];

    let formattedResult = '';
    if (to === 'USD') formattedResult = result.toFixed(2);
    else if (to === 'ARS') formattedResult = Math.round(result).toLocaleString('es-AR');
    else formattedResult = Math.round(result).toLocaleString('es-PY');

    document.getElementById('conv-result').textContent = formattedResult;
}

document.getElementById('conv-amount').addEventListener('input', updateConversion);
document.getElementById('conv-from').addEventListener('change', updateConversion);
document.getElementById('conv-to').addEventListener('change', updateConversion);

fetchRates();
setInterval(fetchRates, 60 * 60 * 1000); // Refresh every hour

