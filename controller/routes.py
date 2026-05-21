"""
controller/routes.py
Define los endpoints REST que consume el frontend.
Sólo habla HTTP: recibe request, llama al controller, devuelve Response.
"""
import os
import tempfile
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file, current_app

from controller.cufe_controller import (
    procesar_archivo_entrada,
    consultar_lista,
    generar_excel,
)
from model.excel_parser import ExcelParserError


api = Blueprint("api", __name__, url_prefix="/api")


# ── POST /api/cargar ──────────────────────────────────────────────────────────
# Recibe el Excel de entrada, extrae los CUFEs y devuelve la lista + metadata.
@api.route("/cargar", methods=["POST"])
def cargar():
    if "archivo" not in request.files:
        return jsonify(error="No se recibió ningún archivo."), 400

    archivo = request.files["archivo"]
    if not archivo.filename:
        return jsonify(error="Nombre de archivo vacío."), 400

    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in (".xlsx", ".xls"):
        return jsonify(error="Solo se aceptan archivos .xlsx o .xls."), 400

    # Guardar en temp para que openpyxl lo pueda leer
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        archivo.save(tmp.name)
        tmp_path = tmp.name

    try:
        resultado = procesar_archivo_entrada(tmp_path)
    except ExcelParserError as e:
        return jsonify(error=str(e)), 422
    finally:
        os.unlink(tmp_path)

    return jsonify(resultado)


# ── POST /api/consultar ───────────────────────────────────────────────────────
# Recibe {"cufes": [...]} y devuelve la lista de resultados.
@api.route("/consultar", methods=["POST"])
def consultar():
    body = request.get_json(silent=True)
    if not body or "cufes" not in body:
        return jsonify(error="Se esperaba {\"cufes\": [...]}"), 400

    cufes = body["cufes"]
    if not isinstance(cufes, list) or len(cufes) == 0:
        return jsonify(error="La lista de CUFEs está vacía."), 400

    resultados = consultar_lista(cufes)
    return jsonify({"resultados": resultados})


# ── POST /api/exportar ────────────────────────────────────────────────────────
# Recibe {"resultados": [...]} y devuelve el archivo Excel para descargar.
@api.route("/exportar", methods=["POST"])
def exportar():
    body = request.get_json(silent=True)
    if not body or "resultados" not in body:
        return jsonify(error="Se esperaba {\"resultados\": [...]}"), 400

    excel_bytes = generar_excel(body["resultados"])
    fecha = datetime.now().strftime("%Y-%m-%d")
    nombre = f"Consulta_DIAN_{fecha}.xlsx"

    return send_file(
        __import__("io").BytesIO(excel_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nombre,
    )
