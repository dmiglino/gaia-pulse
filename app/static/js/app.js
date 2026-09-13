/* GaiaPulse — JS del cliente.
 *
 * No hay bundler ni build step, y no debe introducirse: este archivo se carga
 * con `defer` al final del <body>, después de HTMX/Alpine/Chart.js (que también
 * van con `defer` y por lo tanto se ejecutan en orden de documento).
 */

// ─── Tokens de diseño legibles desde JS ──────────────────────────────────────
// Chart.js pinta sobre canvas, así que no lo alcanzan ni las clases de Tailwind
// ni las variables CSS: necesita colores concretos. En vez de repetir los hex
// (`#64748b` estaba hardcodeado acá y quedaba ilegible en modo oscuro), los lee
// de los mismos tokens que usa el resto de la app.
function tokenColor(name, alpha = 1) {
  const triplet = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!triplet) return alpha === 1 ? '#64748b' : 'rgba(100, 116, 139, ' + alpha + ')';
  const [r, g, b] = triplet.split(/\s+/);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// ─── Tema claro / oscuro ─────────────────────────────────────────────────────
// El tema se aplica *antes* de pintar, en el script síncrono de
// `layouts/shell.html`; acá solo vive el cambio en caliente.
const THEME_KEY = 'gp-theme';

function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

// El chrome del navegador (barra de direcciones en Android, barra de estado en
// iOS) se tinta con `<meta name="theme-color">`. La meta no puede tener `media`
// porque el tema efectivo lo decide localStorage, no el SO — así que la
// sincroniza el JS, leyendo el mismo token que pinta el fondo de la página en vez
// de un hex copiado a mano.
function syncThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', tokenColor('--surface'));
}

function setTheme(theme) {
  const root = document.documentElement;

  // La transición se agrega solo para el cambio manual: si viviera siempre en
  // el CSS, cada carga de página animaría los colores desde el estado inicial.
  root.classList.add('theme-transition');
  root.setAttribute('data-theme', theme);
  root.classList.toggle('dark', theme === 'dark');
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (e) {
    /* modo privado: el tema vale para esta pestaña y nada más */
  }
  window.setTimeout(() => root.classList.remove('theme-transition'), 250);

  syncThemeColor();
  applyChartTheme();
  document.dispatchEvent(new CustomEvent('gp:themechange', { detail: { theme } }));
}

function toggleTheme() {
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

// Delegación: el botón está en la sidebar y también en el perfil (en mobile no
// hay sidebar), y puede llegar dentro de un fragmento de HTMX.
document.addEventListener('click', function (evt) {
  const btn = evt.target.closest('[data-theme-toggle]');
  if (btn) {
    evt.preventDefault();
    toggleTheme();
  }
});

// ─── Chart.js ────────────────────────────────────────────────────────────────
function applyChartTheme() {
  if (!window.Chart) return;
  Chart.defaults.font.family = "'Inter', 'system-ui', sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = tokenColor('--ink-muted');
  Chart.defaults.borderColor = tokenColor('--line');

  // Los gráficos ya montados no releen los defaults por su cuenta.
  document.querySelectorAll('canvas').forEach((canvas) => {
    const chart = Chart.getChart(canvas);
    if (!chart) return;
    if (chart.options.scales) {
      Object.values(chart.options.scales).forEach((scale) => {
        if (scale.ticks) scale.ticks.color = Chart.defaults.color;
        if (scale.grid) scale.grid.color = Chart.defaults.borderColor;
      });
    }
    chart.update('none');
  });
}

applyChartTheme();
syncThemeColor();

// ─── Configuración de HTMX ───────────────────────────────────────────────────
// Estaba en un `DOMContentLoaded` inline dentro de `base.html`, donde las
// páginas standalone (login, errores) no lo veían. La v1 además ponía
// `selfRequestsOnly = false`; se saca, porque ningún `hx-*` de la app apunta a
// otro origen y el default de HTMX (true) es el que corresponde.
if (window.htmx) {
  htmx.config.globalViewTransitions = true;
}

// ─── Toasts ──────────────────────────────────────────────────────────────────
const toastContainer = document.createElement('div');
toastContainer.className = 'toast-container';
toastContainer.setAttribute('role', 'status');
toastContainer.setAttribute('aria-live', 'polite');
document.body.appendChild(toastContainer);

window.showToast = function (message, type = 'info', duration = 4000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 300ms ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
};

// ─── Barra de progreso global ────────────────────────────────────────────────
// `#htmx-indicator` (en `base.html`) casi nunca se encendía: la regla que lo muestra
// es `.htmx-request .htmx-indicator`, que necesita un *ancestro* con `.htmx-request`,
// y de los once `hx-indicator` de la app solo uno lo nombra (el "Refresh" de
// `suggestions/index.html`); los otros diez apuntan a spinners locales. Se cablea acá
// contando requests en vuelo, así que dos pedidos superpuestos no apagan la barra
// antes de tiempo. `app.css` tiene además `.htmx-request.htmx-indicator`, que cubre
// los dos caminos: el que marca HTMX por `hx-indicator` y el que marcamos nosotros.
let inFlight = 0;

function progressBar() {
  return document.getElementById('htmx-indicator');
}

document.addEventListener('htmx:beforeRequest', function () {
  inFlight += 1;
  const bar = progressBar();
  if (bar) bar.classList.add('htmx-request');
});

// Solo `htmx:afterRequest`: HTMX lo dispara para **toda** request que arrancó,
// incluidas las que fallan por red o timeout (esas emiten además `htmx:sendError` /
// `htmx:timeout`, así que escuchar los tres descontaría dos veces y apagaría la
// barra mientras otra request sigue en vuelo).
document.addEventListener('htmx:afterRequest', function () {
  inFlight = Math.max(0, inFlight - 1);
  const bar = progressBar();
  if (bar && inFlight === 0) bar.classList.remove('htmx-request');
});

// ─── Eventos globales de HTMX ────────────────────────────────────────────────
document.addEventListener('htmx:afterRequest', function (evt) {
  const xhr = evt.detail.xhr;
  if (xhr.status >= 400) {
    let msg = 'Something went wrong.';
    try {
      msg = JSON.parse(xhr.responseText)?.detail || msg;
    } catch {}
    showToast(msg, 'error');
  }
});

document.addEventListener('htmx:responseError', function () {
  showToast('Network error. Please try again.', 'error');
});

// `window.VoiceRecorder` vivía acá: 70 líneas de grabación de audio con **cero
// llamadores**. La grabación real la implementa `capture/index.html` en su
// propio componente de Alpine, que es el que habla con `/capture/transcribe`.

window.GP = { setTheme, toggleTheme, currentTheme, tokenColor, applyChartTheme, syncThemeColor };
