"""
server.py
Punto de entrada de la aplicación.
Arranca Flask, registra las rutas y sirve la carpeta view/ como static.
Abre el navegador automáticamente en http://localhost:5000
"""
import threading
import webbrowser
import os
import logging
logging.basicConfig(level=logging.DEBUG)

from flask import Flask, send_from_directory
from controller.routes import api

# ── Configuración ─────────────────────────────────────────────────────────────

PORT  = 5000
HOST  = "127.0.0.1"
DEBUG = False   # Poner True sólo en desarrollo

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
VIEW_DIR  = os.path.join(BASE_DIR, "view")
STATIC_DIR = VIEW_DIR   # CSS y JS se sirven desde view/


# ── App ───────────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    static_folder=STATIC_DIR,
    static_url_path="/static",
)
app.register_blueprint(api)


# ── Rutas de la vista ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(VIEW_DIR, "index.html")


# ── Arranque ──────────────────────────────────────────────────────────────────

def _abrir_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    if not DEBUG:
        # Abrir el browser 1 segundo después de que Flask esté listo
        threading.Timer(1.0, _abrir_browser).start()
        print(f"\n  🚀  Servidor corriendo en http://{HOST}:{PORT}")
        print("      Presiona Ctrl+C para detener.\n")

    app.run(host=HOST, port=PORT, debug=DEBUG)
