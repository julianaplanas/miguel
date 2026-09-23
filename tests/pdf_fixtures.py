"""Genera PDFs de prueba parecidos a un resumen bancario argentino."""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def extracto_con_saldo() -> bytes:
    """Resumen clasico: fecha, concepto, importe y saldo acumulado."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = 280 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "BANCO EJEMPLO - Resumen de cuenta")
    y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, "Periodo: 01/03/2026 al 31/03/2026    Cuenta 123-456/7")
    y -= 10 * mm
    c.drawString(20 * mm, y, "FECHA      CONCEPTO                           IMPORTE          SALDO")
    y -= 6 * mm

    movimientos = [
        ("01/03", "SALDO ANTERIOR", None, "1.250.000,00"),
        ("03/03", "COMPRA COTO DIGITAL", "-48.200,50", "1.201.799,50"),
        ("05/03", "DEBITO AUTOMATICO EDENOR", "-32.150,00", "1.169.649,50"),
        ("10/03", "TRANSFERENCIA RECIBIDA SUELDO", "1.850.000,00", "3.019.649,50"),
        ("15/03", "ALQUILER MARZO", "-480.000,00", "2.539.649,50"),
        ("22/03", "PAGO TARJETA VISA", "-215.400,75", "2.324.248,75"),
    ]
    for fecha, concepto, importe, saldo in movimientos:
        linea = f"{fecha}      {concepto:<35}"
        if importe:
            linea += f"{importe:>15} {saldo:>15}"
        else:
            linea += f"{'':>15} {saldo:>15}"
        c.drawString(20 * mm, y, linea)
        y -= 5.5 * mm

    c.showPage()
    c.save()
    return buffer.getvalue()


def resumen_tarjeta_dolares() -> bytes:
    """Resumen de tarjeta con consumos en pesos y en dolares."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = 280 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "TARJETA EJEMPLO - Resumen 2026")
    y -= 10 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, "FECHA       COMERCIO                          IMPORTE")
    y -= 6 * mm

    consumos = [
        ("04/02/2026", "MERCADOPAGO*VERDULERIA", "$ 18.400,00"),
        ("07/02/2026", "NETFLIX.COM", "US$ 9,99"),
        ("12/02/2026", "YPF SERVICIOS", "$ 45.000,00"),
        ("18/02/2026", "AWS AMAZON WEB SERVICES", "US$ 42,30"),
        ("25/02/2026", "FARMACITY", "$ 12.750,80"),
    ]
    for fecha, comercio, importe in consumos:
        c.drawString(20 * mm, y, f"{fecha}  {comercio:<35}{importe:>15}")
        y -= 5.5 * mm

    c.showPage()
    c.save()
    return buffer.getvalue()


def pdf_escaneado() -> bytes:
    """PDF sin capa de texto (solo un rectangulo): simula un escaneo."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.rect(20 * mm, 200 * mm, 100 * mm, 50 * mm, fill=0)
    c.showPage()
    c.save()
    return buffer.getvalue()
