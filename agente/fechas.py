import calendar
import os
from datetime import date, datetime, timedelta, timezone
from agente.feriados import FERIADOS

# Hora argentina (UTC-3, sin horario de verano).
ZONA_ART = timezone(timedelta(hours=-3))

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def hoy():
    simulada = os.getenv("FECHA_SIMULADA")
    return datetime.strptime(simulada, "%Y-%m-%d").date() if simulada else datetime.now().date()


def parsear(fecha_str):
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parsear_iso_utc(texto):
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def formatear_larga(fecha_str):
    fecha = parsear(fecha_str)
    return f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}" if fecha else fecha_str


def formatear_corta(fecha_str):
    fecha = parsear(fecha_str)
    return fecha.strftime("%d/%m/%Y") if fecha else fecha_str


def fecha_hora_art(momento):
    return momento.astimezone(ZONA_ART).strftime("%d/%m/%Y %H:%M")


def es_dia_habil(fecha):
    return fecha.weekday() < 5 and fecha not in FERIADOS


def _mover_dias_habiles(fecha, dias_habiles, paso):
    actual = fecha
    restantes = dias_habiles
    while restantes > 0:
        actual += timedelta(days=paso)
        if es_dia_habil(actual):
            restantes -= 1
    return actual


def restar_dias_habiles(fecha, dias_habiles):
    return _mover_dias_habiles(fecha, dias_habiles, -1)


def sumar_dias_habiles(fecha, dias_habiles):
    return _mover_dias_habiles(fecha, dias_habiles, 1)


def fin_del_mes_siguiente(fecha):
    anio, mes = (fecha.year + 1, 1) if fecha.month == 12 else (fecha.year, fecha.month + 1)
    return date(anio, mes, calendar.monthrange(anio, mes)[1])
