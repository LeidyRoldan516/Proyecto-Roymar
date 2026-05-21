"""
model/excel_parser.py
Lee el archivo Excel de entrada y extrae la lista de CUFEs,
detectando automáticamente la columna correcta.
"""
import re
import openpyxl


# CUFE/CUDE: cadena hexadecimal de 96 caracteres (factura) o similar (otros docs)
_PATRON_CUFE = re.compile(r'^[a-f0-9]{48,}$', re.IGNORECASE)
_ENCABEZADOS_CUFE = re.compile(r'cufe|cude', re.IGNORECASE)


class ExcelParserError(Exception):
    pass


def extraer_cufes(ruta_archivo: str) -> dict:
    """
    Lee el primer sheet del Excel y devuelve:
    {
        "cufes": [...],          # lista de strings, sin duplicados
        "columna": "CUFE/CUDE", # nombre de la columna detectada
        "tiene_encabezado": True,
        "total_filas": 120,
    }
    Lanza ExcelParserError si no puede encontrar CUFEs.
    """
    try:
        wb = openpyxl.load_workbook(ruta_archivo, read_only=True, data_only=True)
    except Exception as e:
        raise ExcelParserError(f"No se pudo abrir el archivo: {e}")

    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    wb.close()

    if not filas:
        raise ExcelParserError("El archivo Excel está vacío.")

    primera_fila = [str(v).strip() if v is not None else "" for v in filas[0]]

    # ── 1. Buscar columna por encabezado ─────────────────────────
    col_idx = -1
    col_nombre = ""
    tiene_encabezado = False
    inicio_datos = 0

    for i, celda in enumerate(primera_fila):
        if _ENCABEZADOS_CUFE.search(celda):
            col_idx = i
            col_nombre = celda
            tiene_encabezado = True
            inicio_datos = 1
            break

    # ── 2. Sin encabezado: buscar primera columna con valores tipo CUFE ──
    if col_idx == -1:
        for i, celda in enumerate(primera_fila):
            if _PATRON_CUFE.match(celda):
                col_idx = i
                col_nombre = f"Columna {i + 1}"
                tiene_encabezado = False
                inicio_datos = 0
                break

    # ── 3. Fallback: columna A ────────────────────────────────────
    if col_idx == -1:
        col_idx = 0
        col_nombre = primera_fila[0] if primera_fila[0] else "Columna A"
        tiene_encabezado = not _PATRON_CUFE.match(primera_fila[0])
        inicio_datos = 1 if tiene_encabezado else 0

    # ── Extraer valores ───────────────────────────────────────────
    cufes = []
    vistos = set()
    for fila in filas[inicio_datos:]:
        if col_idx >= len(fila):
            continue
        val = str(fila[col_idx]).strip() if fila[col_idx] is not None else ""
        if _PATRON_CUFE.match(val) and val not in vistos:
            cufes.append(val)
            vistos.add(val)

    if not cufes:
        raise ExcelParserError(
            f"No se encontraron CUFEs en la columna «{col_nombre}». "
            "Verifica que los valores sean cadenas hexadecimales de al menos 48 caracteres."
        )

    return {
        "cufes": cufes,
        "columna": col_nombre,
        "tiene_encabezado": tiene_encabezado,
        "total_filas": len(filas) - inicio_datos,
    }
