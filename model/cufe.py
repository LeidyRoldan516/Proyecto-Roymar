"""
model/cufe.py
Dataclass que representa un CUFE y el resultado de su consulta a la DIAN.
"""
from dataclasses import dataclass, field


@dataclass
class CufeResultado:
    # ── Identificación ──────────────────────────────────────────
    cufe: str = ""

    # ── Tipo y numeración ───────────────────────────────────────
    tipo_documento: str = ""
    folio: str = ""
    prefijo: str = ""

    # ── Moneda y pago ───────────────────────────────────────────
    divisa: str = "COP"
    forma_pago: str = ""
    medio_pago: str = ""

    # ── Fechas ──────────────────────────────────────────────────
    fecha_emision: str = ""
    fecha_recepcion: str = ""

    # ── Emisor ──────────────────────────────────────────────────
    nit_emisor: str = ""
    nombre_emisor: str = ""

    # ── Receptor ────────────────────────────────────────────────
    nit_receptor: str = ""
    nombre_receptor: str = ""

    # ── Impuestos ───────────────────────────────────────────────
    iva: float = 0.0
    ica: float = 0.0
    ic: float = 0.0
    inc: float = 0.0
    timbre: float = 0.0
    inc_bolsas: float = 0.0
    in_carbono: float = 0.0
    in_combustibles: float = 0.0
    ic_datos: float = 0.0
    icl: float = 0.0
    inpp: float = 0.0
    ibua: float = 0.0
    icui: float = 0.0

    # ── Retenciones ─────────────────────────────────────────────
    rete_iva: float = 0.0
    rete_renta: float = 0.0
    rete_ica: float = 0.0

    # ── Totales y estado ────────────────────────────────────────
    total: float = 0.0
    estado: str = ""
    grupo: str = "Emitido"

    # ── Meta (no va al Excel) ───────────────────────────────────
    tiene_datos: bool = False
    error: str = ""

    def to_fila_excel(self) -> list:
        """Devuelve una lista ordenada lista para escribir en Excel."""
        return [
            self.tipo_documento, self.cufe, self.folio, self.prefijo,
            self.divisa, self.forma_pago, self.medio_pago,
            self.fecha_emision, self.fecha_recepcion,
            self.nit_emisor, self.nombre_emisor,
            self.nit_receptor, self.nombre_receptor,
            self.iva, self.ica, self.ic, self.inc, self.timbre,
            self.inc_bolsas, self.in_carbono, self.in_combustibles,
            self.ic_datos, self.icl, self.inpp, self.ibua, self.icui,
            self.rete_iva, self.rete_renta, self.rete_ica,
            self.total, self.estado, self.grupo,
        ]

    @staticmethod
    def cabeceras_excel() -> list:
        return [
            "Tipo de documento", "CUFE/CUDE", "Folio", "Prefijo", "Divisa",
            "Forma de Pago", "Medio de Pago", "Fecha Emisión", "Fecha Recepción",
            "NIT Emisor", "Nombre Emisor", "NIT Receptor", "Nombre Receptor",
            "IVA", "ICA", "IC", "INC", "Timbre", "INC Bolsas", "IN Carbono",
            "IN Combustibles", "IC Datos", "ICL", "INPP", "IBUA", "ICUI",
            "Rete IVA", "Rete Renta", "Rete ICA", "Total", "Estado", "Grupo",
        ]
