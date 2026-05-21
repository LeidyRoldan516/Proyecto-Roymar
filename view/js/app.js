/**
 * view/js/app.js
 * Bootstrap: conecta Uploader, Consulta y Exporter.
 * Gestiona el estado de los pasos y la visibilidad de las secciones.
 */

document.addEventListener('DOMContentLoaded', () => {

  // ── Estado ─────────────────────────────────────────────────────
  let _cufes      = [];
  let _resultados = [];

  // ── Módulos ────────────────────────────────────────────────────
  Uploader.init({
    onReady: (cufes) => {
      _cufes = cufes;
      document.getElementById('btn-start').disabled = false;
    },
    onReset: () => {
      _cufes = [];
      document.getElementById('btn-start').disabled = true;
    },
  });

  Consulta.init({
    onFin: (resultados, stats) => {
      _resultados = resultados;
      _mostrarResultados(resultados, stats);
      setStep(3);
      _mostrarSeccion('results-section');
      _ocultarSeccion('progress-section');
    },
  });

  // ── Botones ────────────────────────────────────────────────────
  document.getElementById('btn-start').addEventListener('click', async () => {
    if (_cufes.length === 0) return;
    setStep(2);
    _ocultarSeccion('card-upload');
    _mostrarSeccion('progress-section');
    await Consulta.iniciar(_cufes);
  });

  document.getElementById('btn-export').addEventListener('click', () => {
    Exporter.descargar(_resultados);
  });

  document.getElementById('btn-restart').addEventListener('click', () => {
    _cufes      = [];
    _resultados = [];
    _ocultarSeccion('results-section');
    _ocultarSeccion('progress-section');
    _mostrarSeccion('card-upload');
    Uploader.reset();
    document.getElementById('btn-start').disabled = true;
    document.getElementById('log-wrap').innerHTML  = '';
    document.getElementById('warn-errors').style.display = 'none';
    setStep(1);
  });

  // ── Steps ──────────────────────────────────────────────────────
  function setStep(n) {
    [1, 2, 3].forEach(i => {
      const el = document.getElementById(`step-${i}`);
      el.classList.remove('active', 'done');
      if (i < n)      el.classList.add('done');
      else if (i === n) el.classList.add('active');
    });
  }

  // ── Resultados ─────────────────────────────────────────────────
  function _mostrarResultados(resultados, stats) {
    document.getElementById('stat-total').textContent = resultados.length;
    document.getElementById('stat-ok').textContent    = stats.ok;
    document.getElementById('stat-err').textContent   = stats.err;
    document.getElementById('stat-skip').textContent  = stats.skip;

    if (stats.err > 0) {
      document.getElementById('warn-errors').style.display = 'block';
    }

    // Tabla de preview
    const cols = ['Tipo', 'CUFE', 'Folio', 'Emisor', 'Receptor', 'Total', 'Estado'];
    let html = `<table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>`;

    resultados.slice(0, 30).forEach(r => {
      const ok = r.tiene_datos;
      html += `<tr>
        <td>${r.tipo_documento || '—'}</td>
        <td title="${r.cufe}" style="font-family:'DM Mono',monospace;font-size:10px">${r.cufe.slice(0, 20)}…</td>
        <td>${r.prefijo ? r.prefijo + '-' : ''}${r.folio || '—'}</td>
        <td>${r.nombre_emisor || '—'}</td>
        <td>${r.nombre_receptor || '—'}</td>
        <td style="text-align:right">${r.total ? '$' + Number(r.total).toLocaleString('es-CO') : '—'}</td>
        <td><span class="badge ${ok ? 'badge-ok' : 'badge-err'}">${ok ? '✓' : '✗'} ${(r.estado || '?').slice(0, 20)}</span></td>
      </tr>`;
    });

    if (resultados.length > 30) {
      html += `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:12px">… y ${resultados.length - 30} registros más en el Excel</td></tr>`;
    }
    html += '</tbody></table>';
    document.getElementById('preview-table-wrap').innerHTML = html;
  }

  // ── Helpers de visibilidad ─────────────────────────────────────
  function _mostrarSeccion(id) { document.getElementById(id).style.display = 'block'; }
  function _ocultarSeccion(id) { document.getElementById(id).style.display = 'none'; }

});
