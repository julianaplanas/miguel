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


def resumen_tarjeta_dos_columnas() -> bytes:
    """Resumen de tarjeta con columnas separadas de PESOS y DOLARES.

    Reproduce lo que complica a un lector de texto plano: la fecha con el
    mes en letras (29-Jul-26), un numero de comprobante antes del importe,
    importes escritos dentro de la descripcion, y el importe de la linea
    cayendo en una columna o en la otra segun la moneda. En texto plano las
    dos columnas quedan pegadas y no hay forma de saber cual es cual.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    x_fecha, x_desc, x_comp = 20 * mm, 40 * mm, 130 * mm
    x_pesos, x_dolares = 170 * mm, 195 * mm  # borde derecho: van alineados

    y = 280 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x_fecha, y, "Tarjeta Credito MASTERCARD - Resumen N 0270103")
    y -= 10 * mm
    c.setFont("Helvetica", 8)
    c.drawString(x_fecha, y, "CONSOLIDADO")
    c.drawRightString(x_pesos, y, "PESOS")
    c.drawRightString(x_dolares, y, "DOLARES")
    y -= 5 * mm
    c.drawString(x_fecha, y, "SALDO ANTERIOR")
    c.drawRightString(x_pesos, y, "1.648.746,33")
    y -= 5 * mm
    c.drawString(x_fecha, y, "31-Jul-26")
    c.drawString(x_desc, y, "SU PAGO")
    c.drawRightString(x_pesos, y, "-1.653.873,20")
    y -= 10 * mm

    c.drawString(x_fecha, y, "FECHA")
    c.drawString(x_desc, y, "REFERENCIA")
    c.drawString(x_comp, y, "COMPROBANTE")
    c.drawRightString(x_pesos, y, "PESOS")
    c.drawRightString(x_dolares, y, "DOLARES")
    y -= 6 * mm

    consumos = [
        ("29-Jul-26", "APPLE.COM/BILL (USA,USD, 0,99)", "00322", None, "0,99"),
        ("10-Ago-26", "Spotify (SWE,ARS, 5499,00)", "00217", None, "3,67"),
        ("22-Jul-26", "WWW1.HOSPITALITALIANO", "08783", "21.425,71", None),
        ("05-Ago-26", "MERPAGO*COCACOLAENTUC", "07859", "286.740,00", None),
        ("12-Sep-25", "DLO*DUVET HOME 12/12", "02402", "77.413,75", None),
    ]
    for fecha, desc, comprobante, pesos, dolares in consumos:
        c.drawString(x_fecha, y, fecha)
        c.drawString(x_desc, y, desc)
        c.drawString(x_comp, y, comprobante)
        if pesos:
            c.drawRightString(x_pesos, y, pesos)
        if dolares:
            c.drawRightString(x_dolares, y, dolares)
        y -= 5.5 * mm

    y -= 4 * mm
    # Este total es EXACTAMENTE la suma de los consumos de las dos hojas:
    # es lo que deja comprobar que la lectura no se dejo ninguno.
    c.drawString(x_desc, y, "TOTAL A PAGAR")
    c.drawRightString(x_pesos, y, "420.579,46")
    c.drawRightString(x_dolares, y, "46,96")

    # Segunda hoja: sigue el detalle pero SIN repetir la cabecera, como
    # hacen muchos resumenes. Las columnas son las mismas.
    c.showPage()
    c.setFont("Helvetica", 8)
    y = 280 * mm
    c.drawString(x_fecha, y, "Tarjeta Credito MASTERCARD - continuacion")
    y -= 10 * mm
    siguen = [
        ("18-Ago-26", "AWS AMAZON WEB SERVICES", "04102", None, "42,30"),
        ("19-Ago-26", "TOTALGAS SRL", "04110", "35.000,00", None),
        # Linea de totales con fecha: no es un gasto aunque lo parezca.
        ("20-Ago-26", "TOTAL DEL MES", "", "421.579,46", "46,96"),
    ]
    for fecha, desc, comprobante, pesos, dolares in siguen:
        c.drawString(x_fecha, y, fecha)
        c.drawString(x_desc, y, desc)
        if comprobante:
            c.drawString(x_comp, y, comprobante)
        if pesos:
            c.drawRightString(x_pesos, y, pesos)
        if dolares:
            c.drawRightString(x_dolares, y, dolares)
        y -= 5.5 * mm

    c.showPage()
    c.save()
    return buffer.getvalue()


def resumen_dos_titulares() -> bytes:
    """Resumen de tarjeta con dos titulares y cabecera con simbolos.

    Reproduce lo que rompia al lector por posicion: la cabecera de las
    columnas no dice PESOS/DOLARES sino "$" y "U$S", el detalle se parte en
    secciones por titular (con subtotales en medio), algunas lineas llevan
    el simbolo de la moneda como palabra suelta delante del importe, y en
    la segunda hoja no se repite la cabecera.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    x_fecha, x_desc, x_cupon = 18 * mm, 38 * mm, 128 * mm
    x_pesos, x_dolares = 168 * mm, 196 * mm

    def cabecera(y):
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x_fecha, y, "FECHA")
        c.drawString(x_desc, y, "DETALLE")
        c.drawString(x_cupon, y, "CUPON")
        c.drawRightString(x_pesos, y, "$")
        c.drawRightString(x_dolares, y, "U$S")
        c.setFont("Helvetica", 8)

    def movimiento(y, fecha, desc, cupon, pesos=None, dolares=None, simbolo=False):
        c.drawString(x_fecha, y, fecha)
        c.drawString(x_desc, y, desc)
        c.drawString(x_cupon, y, cupon)
        if pesos:
            if simbolo:
                c.drawRightString(x_pesos - 24 * mm, y, "$")
            c.drawRightString(x_pesos, y, pesos)
        if dolares:
            if simbolo:
                c.drawRightString(x_dolares - 16 * mm, y, "U$S")
            c.drawRightString(x_dolares, y, dolares)

    y = 275 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_fecha, y, "VISA - Resumen de cuenta 2026")
    y -= 12 * mm
    cabecera(y)
    y -= 6 * mm

    c.drawString(x_desc, y, "MIGUEL ALEJA PLANAS 4517 XXXX XXXX 1234")
    y -= 5.5 * mm
    for fecha, desc, cupon, pesos, dolares in [
        ("02-Ago-26", "MERPAGO*LIBRERIA", "00121", "12.500,00", None),
        ("05-Ago-26", "OPENAI *CHATGPT", "00122", None, "20,00"),
        ("11-Ago-26", "SUBTE SUBE", "00123", "8.000,00", None),
    ]:
        movimiento(y, fecha, desc, cupon, pesos, dolares, simbolo=True)
        y -= 5.5 * mm
    y -= 2 * mm
    c.drawString(x_desc, y, "Subtotal de Miguel Aleja Planas")
    c.drawRightString(x_pesos, y, "20.500,00")
    c.drawRightString(x_dolares, y, "20,00")

    # Segunda hoja: otro titular y ninguna cabecera.
    c.showPage()
    c.setFont("Helvetica", 8)
    y = 275 * mm
    c.drawString(x_desc, y, "MARIA DE ZENOBI 4517 XXXX XXXX 5678")
    y -= 6 * mm
    for fecha, desc, cupon, pesos, dolares in [
        ("03-Ago-26", "FARMACITY", "00201", "35.400,50", None),
        ("09-Ago-26", "NETFLIX.COM", "00202", None, "12,99"),
        ("15-Ago-26", "COTO CICSA", "00203", "89.100,00", None),
        ("21-Ago-26", "YPF FULL", "00204", "47.000,00", None),
    ]:
        movimiento(y, fecha, desc, cupon, pesos, dolares)
        y -= 5.5 * mm
    y -= 2 * mm
    c.drawString(x_desc, y, "Subtotal de Maria De Zenobi")
    c.drawRightString(x_pesos, y, "171.500,50")
    c.drawRightString(x_dolares, y, "12,99")
    y -= 8 * mm
    c.drawString(x_desc, y, "Total a pagar")
    c.drawRightString(x_pesos, y, "192.000,50")
    c.drawRightString(x_dolares, y, "32,99")

    c.showPage()
    c.save()
    return buffer.getvalue()
