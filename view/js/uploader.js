/**
 * view/js/uploader.js
 * Gestiona el drag & drop y la lectura del Excel de entrada.
 * Llama a /api/cargar en el backend para parsear el archivo.
 */

const Uploader = (() => {
  let _onReady = null;   // callback(cufes, meta)
  let _onReset = null;   // callback()

  function init({ onReady, onReset }) {
    _onReady = onReady;
    _onReset = onReset;

    const dropzone = document.getElementById('dropzone');
    const input    = document.getElementById('xlsx-input');

    dropzone.addEventListener('dragover', e => {
      e.preventDefault();
      dropzone.classList.add('drag-over');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('drag-over');
      const f = e.dataTransfer.files[0];
      if (f) _handleFile(f);
    });

    input.addEventListener('change', e => {
      if (e.target.files[0]) _handleFile(e.target.files[0]);
    });

    document.getElementById('file-remove-btn').addEventListener('click', reset);
  }

  async function _handleFile(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['xlsx', 'xls'].includes(ext)) {
      alert('Solo se aceptan archivos .xlsx o .xls');
      return;
    }

    // Subir al backend para parsear
    const form = new FormData();
    form.append('archivo', file);

    let data;
    try {
      const resp = await fetch('/api/cargar', { method: 'POST', body: form });
      data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    } catch (err) {
      alert('Error al leer el archivo:\n' + err.message);
      return;
    }

    _mostrarPreview(file, data);
    _onReady && _onReady(data.cufes, data);
  }

  function _mostrarPreview(file, data) {
    document.getElementById('file-name-text').textContent = file.name;
    document.getElementById('file-meta-text').textContent =
      `${(file.size / 1024).toFixed(1)} KB · columna "${data.columna}" · ${data.cufes.length} CUFEs`;

    document.getElementById('file-preview').classList.add('show');

    const section = document.getElementById('cufe-preview-section');
    section.style.display = 'block';
    document.getElementById('cufe-count-num').textContent = data.cufes.length;

    const list = document.getElementById('cufe-list-preview');
    list.innerHTML = '';
    data.cufes.slice(0, 50).forEach((c, i) => {
      list.innerHTML +=
        `<div class="cufe-line">${String(i + 1).padStart(3, '0')}  ` +
        `<span>${c.slice(0, 32)}…${c.slice(-8)}</span></div>`;
    });
    if (data.cufes.length > 50) {
      list.innerHTML += `<div style="color:var(--muted);margin-top:4px">… y ${data.cufes.length - 50} más</div>`;
    }
  }

  function reset() {
    document.getElementById('xlsx-input').value = '';
    document.getElementById('file-preview').classList.remove('show');
    document.getElementById('cufe-preview-section').style.display = 'none';
    _onReset && _onReset();
  }

  return { init, reset };
})();
