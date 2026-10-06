// Utilidades compartidas
async function api(url, opts = {}) {
  const o = { headers: { 'X-Requested-With': 'fetch' }, ...opts };
  if (o.json !== undefined) {
    o.method = o.method || 'POST';
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.json);
    delete o.json;
  }
  const r = await fetch(url, o);
  if (r.status === 401) { location.href = '/login'; throw new Error('Sesión vencida'); }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    let msg = (data && (data.detail || data.error)) || ('Error ' + r.status);
    if (r.status === 404 && msg === 'Not Found')
      msg = 'La app abierta es una versión anterior: tocá "Detener y salir" en la ventanita de PARKA Depósito y abrila de nuevo';
    if (Array.isArray(msg)) msg = msg.map(m => m.msg).join(' · ');
    throw new Error(msg);
  }
  return data;
}

function toast(msg, tipo = 'ok', ms = 3000) {
  const cols = { ok: 'bg-teal-700', error: 'bg-red-700', warn: 'bg-amber-700', info: 'bg-violeta' };
  const el = document.createElement('div');
  el.className = 'toast ' + (cols[tipo] || cols.info);
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Sonidos (igual que el scanner)
let _ac = null;
function _tone(freq, dur, type = 'sine', vol = 0.3) {
  try {
    _ac = _ac || new (window.AudioContext || window.webkitAudioContext)();
    const o = _ac.createOscillator(), g = _ac.createGain();
    o.type = type; o.frequency.value = freq; g.gain.value = vol;
    o.connect(g); g.connect(_ac.destination);
    o.start(); g.gain.exponentialRampToValueAtTime(0.0001, _ac.currentTime + dur);
    o.stop(_ac.currentTime + dur);
  } catch {}
}
const beepOk = () => { _tone(880, 0.12); setTimeout(() => _tone(1320, 0.15), 110); if (navigator.vibrate) navigator.vibrate(60); };
const beepError = () => { _tone(220, 0.35, 'square', 0.25); if (navigator.vibrate) navigator.vibrate([120, 60, 120]); };
const beepWarn = () => { _tone(520, 0.2, 'triangle'); setTimeout(() => _tone(520, 0.2, 'triangle'), 250); if (navigator.vibrate) navigator.vibrate(200); };

// Etiquetas: se imprimen desde una página HTML (misma grilla y diseño que el PDF) que abre sola
// el diálogo de impresión. No depende del visor de PDF del navegador ni de pestañas nuevas.
const esCelular = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
async function imprimirPDF(url, btn) {
  const urlHtml = url.replace('/etiquetas.pdf', '/etiquetas.html');
  if (esCelular) { location.href = urlHtml; return; }
  const txt = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = 'Preparando etiquetas…'; }
  try {
    const r = await fetch(urlHtml, { headers: { 'X-Requested-With': 'fetch' } });
    if (r.status === 401) { location.href = '/login'; return; }
    const html = await r.text();
    if (!r.ok) {
      let msg = 'Error ' + r.status;
      try { const d = JSON.parse(html); msg = d.detail || d.error || msg; } catch {}
      if (r.status === 404) msg = 'La app abierta es una versión anterior: cerrala ("Detener y salir") y abrila de nuevo';
      throw new Error('No se pudieron generar las etiquetas: ' + msg);
    }
    document.getElementById('frame-imprimir')?.remove();
    const f = document.createElement('iframe');
    f.id = 'frame-imprimir';
    f.style.cssText = 'position:fixed;left:-10000px;top:0;width:210mm;height:297mm;border:0;';
    document.body.appendChild(f);
    f.srcdoc = html;   // la página llama sola a window.print() cuando cargan las fuentes
    toast('Abriendo la ventana de impresión…', 'info', 2500);
  } catch (e) {
    toast(e.message, 'error', 7000);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = txt; }
  }
}

function chipTarea(t) {
  let h = '';
  if (t.vencida) h += '<span class="chip chip-vencida">VENCIDA</span> ';
  else h += `<span class="chip chip-${t.estado}">${{ pendiente: 'PENDIENTE', en_curso: 'EN CURSO', hecha: 'HECHA' }[t.estado]}</span> `;
  if (t.prioridad === 'alta') h += '<span class="chip chip-alta">ALTA</span> ';
  if (t.origen === 'auto') h += '<span class="chip chip-auto">AUTO</span> ';
  return h;
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

// Marca en el menú la sección actual
document.querySelectorAll('.hdr .nav-link').forEach(a => {
  const h = a.getAttribute('href');
  if (h === '/' ? location.pathname === '/' : location.pathname.startsWith(h)) a.classList.add('activo');
});

// Catálogo común (artículos de Odoo con código de barras). Para usar desde cualquier sección:
//   const a = await Catalogo.codigo('2000000016603');   // null si no existe
//   const {resultados, exacto} = await Catalogo.buscar('thor negro xl');
//   const {variantes} = await Catalogo.modelo('THOR 00114');
//   const {stock} = await Catalogo.stock([a.id]);        // stock actual en Odoo
const Catalogo = {
  async buscar(q, { limite = 100, marca = '', conCodigo = false } = {}) {
    const p = new URLSearchParams({ q, limite, marca, con_codigo: conCodigo });
    return api('/api/catalogo/buscar?' + p);
  },
  async codigo(codigo) {
    try { return await api('/api/catalogo/codigo/' + encodeURIComponent(String(codigo).trim())); }
    catch (e) { if (/no encontrado/i.test(e.message)) return null; throw e; }
  },
  articulo(id) { return api('/api/catalogo/articulo/' + id); },
  modelo(articulo) { return api('/api/catalogo/modelo?articulo=' + encodeURIComponent(articulo)); },
  stock(ids) { return api('/api/catalogo/stock', { json: { ids } }); },
  estado() { return api('/api/catalogo/estado'); },
};
window.Catalogo = Catalogo;
