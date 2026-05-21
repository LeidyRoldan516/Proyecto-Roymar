/**
 * view/js/consulta.js
 * Maneja el flujo de consulta: divide la lista en lotes,
 * llama a /api/consultar y actualiza la UI en tiempo real.
 */

const Consulta = (() => {
  const LOTE = 10;   // CUFEs por request al backend (el backend los paraleliza)

  let _resultados = [];
  let _stats      = { ok: 0, err: 0, skip: 0 };
  let _detenido   = false;
  let _onFin      = null;   // callback(resultados)

  function init({ onFin }) {
    _onFin = onFin;
    document.getElementById('btn-stop').addEventListener('click', () => {
      _detenido = true;
      _log('⏹ Deteniendo… espera el lote actual.', 'warn');
    });
  }

  async function iniciar(cufes) {
    _resultados = [];
    _stats      = { ok: 0, err: 0, skip: 0 };
    _detenido   = false;
    document.getElementById('log-wrap').innerHTML = '';

    const total = cufes.length;
    _actualizarProgreso(0, total);
    _log(`🚀 Iniciando consulta de ${total} CUFEs en lotes de ${LOTE}…`, 'info');

    let done = 0;
    const lotes = _chunks(cufes, LOTE);

    for (const lote of lotes) {
      if (_detenido) break;

      let data;
      try {
        const resp = await fetch('/api/consultar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cufes: lote }),
        });
        data = await resp.json();
        if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      } catch (err) {
        // Error de red completo para el lote
        lote.forEach(cufe => {
          _resultados.push({ cufe, tiene_datos: false, error: err.message, estado: `Error: ${err.message}` });
          _stats.err++;
          _log(`❌ Lote fallido: ${err.message}`, 'err');
        });
        done += lote.length;
        _actualizarProgreso(done, total);
        continue;
      }

      data.resultados.forEach(r => {
        _resultados.push(r);
        if (r.error) {
          _stats.err++;
          _log(`❌ ${_corto(r.cufe)} · ${r.error}`, 'err');
        } else if (r.tiene_datos) {
          _stats.ok++;
          _log(
            `✅ ${_corto(r.cufe)} · ${r.nombre_emisor || '?'} → ${r.nombre_receptor || '?'}` +
            ` · $${Number(r.total).toLocaleString('es-CO')}`,
            'ok'
          );
        } else {
          _stats.skip++;
          _log(`⚠️ ${_corto(r.cufe)} · Sin datos estructurados`, 'warn');
        }
      });

      done += lote.length;
      _actualizarProgreso(done, total);
    }

    const fin = _detenido ? 'Detenida por el usuario' : 'Completada';
    _log(
      `🏁 ${fin}: ${_stats.ok} exitosos, ${_stats.err} errores, ${_stats.skip} sin datos.`,
      'info'
    );
    _onFin && _onFin(_resultados, _stats);
  }

  // ── Helpers ───────────────────────────────────────────────────

  function _actualizarProgreso(done, total) {
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    document.getElementById('progress-fill').style.width  = pct + '%';
    document.getElementById('prog-done').textContent  = done;
    document.getElementById('prog-total').textContent = total;
    document.getElementById('prog-pct').textContent   = pct;
    document.getElementById('prog-ok').textContent    = _stats.ok;
    document.getElementById('prog-err').textContent   = _stats.err;
    document.getElementById('prog-skip').textContent  = _stats.skip;
  }

  function _log(msg, tipo = 'info') {
    const now  = new Date().toLocaleTimeString('es-CO', { hour12: false });
    const cls  = { ok: 'log-ok', err: 'log-err', warn: 'log-warn', info: 'log-info' }[tipo] || 'log-info';
    const wrap = document.getElementById('log-wrap');
    wrap.innerHTML += `<div class="log-line"><span class="log-time">${now}</span><span class="${cls}">${msg}</span></div>`;
    wrap.scrollTop  = wrap.scrollHeight;
  }

  function _corto(cufe) {
    return cufe.slice(0, 16) + '…';
  }

  function _chunks(arr, size) {
    const out = [];
    for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
    return out;
  }

  return { init, iniciar };
})();
