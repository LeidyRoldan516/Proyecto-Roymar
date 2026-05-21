/**
 * view/js/exporter.js
 * Envía los resultados al backend y descarga el Excel generado.
 */

const Exporter = (() => {

  async function descargar(resultados) {
    try {
      const resp = await fetch('/api/exportar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resultados }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.error || `HTTP ${resp.status}`);
      }

      // Disparar descarga en el browser
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      const fecha = new Date().toISOString().slice(0, 10);

      a.href     = url;
      a.download = `Consulta_DIAN_${fecha}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('Error al exportar:\n' + err.message);
    }
  }

  return { descargar };
})();
