"""Revisa las actividades con fecha de entrega en Moodle y avisa por ntfy.

Manda un aviso cuando aparece una actividad nueva y recordatorios antes
de la fecha límite. Está pensado para ejecutarse periódicamente (cron,
Programador de tareas o GitHub Actions).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
ESTADO_PATH = BASE_DIR / "avisados.json"
LIMITE_POR_PAGINA = 50  # máximo que acepta Moodle para esta función
CLAVE_ALERTA = "alerta:error"  # marca que ya se avisó de un error de Moodle

log = logging.getLogger("moodle_avisos")


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    moodle_url: str
    token: str
    ntfy_url: str
    ntfy_topic: str
    horas_recordatorio: list[int]
    zona: ZoneInfo


def cargar_config() -> Config:
    """Lee la configuración desde .env y variables de entorno."""
    load_dotenv(BASE_DIR / ".env")

    faltantes = [v for v in ("MOODLE_TOKEN", "NTFY_TOPIC") if not os.getenv(v)]
    if faltantes:
        raise SystemExit(f"Faltan variables en .env: {', '.join(faltantes)}")

    horas = os.getenv("RECORDATORIOS_HORAS", "24,2")
    return Config(
        moodle_url=os.getenv("MOODLE_URL", "https://cursos.iberoleon.mx/online").rstrip("/"),
        token=os.environ["MOODLE_TOKEN"],
        ntfy_url=os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/"),
        ntfy_topic=os.environ["NTFY_TOPIC"],
        horas_recordatorio=sorted(int(h) for h in horas.split(",")),
        zona=ZoneInfo(os.getenv("ZONA_HORARIA", "America/Mexico_City")),
    )


# --------------------------------------------------------------------------
# Moodle
# --------------------------------------------------------------------------

class MoodleError(Exception):
    """Moodle respondió con un error (token inválido, función no permitida...)."""


def llamar_moodle(cfg: Config, funcion: str, **params) -> dict:
    """Llama a una función de la API REST de Moodle y devuelve el JSON."""
    resp = requests.post(
        f"{cfg.moodle_url}/webservice/rest/server.php",
        data={
            "wstoken": cfg.token,
            "wsfunction": funcion,
            "moodlewsrestformat": "json",
            **params,
        },
        timeout=20,
    )
    resp.raise_for_status()
    datos = resp.json()

    # Moodle responde 200 OK incluso cuando hay error; el error viene en el JSON.
    if isinstance(datos, dict) and "exception" in datos:
        raise MoodleError(f"{datos.get('errorcode')}: {datos.get('message')}")
    return datos


def obtener_eventos(cfg: Config, desde: int) -> list[dict]:
    """Devuelve todas las actividades con fecha a partir de `desde` (paginando)."""
    eventos: list[dict] = []
    despues_de = 0

    while True:
        params = {"timesortfrom": desde, "limitnum": LIMITE_POR_PAGINA}
        if despues_de:
            params["aftereventid"] = despues_de

        datos = llamar_moodle(cfg, "core_calendar_get_action_events_by_timesort", **params)
        lote = datos.get("events", [])
        eventos.extend(lote)

        if len(lote) < LIMITE_POR_PAGINA:
            return eventos
        despues_de = datos["lastid"]


def es_pendiente(evento: dict) -> bool:
    """Descarta actividades que ya no requieren acción (p. ej. ya entregadas)."""
    accion = evento.get("action")
    return accion is None or accion.get("actionable", True)


# --------------------------------------------------------------------------
# Notificaciones
# --------------------------------------------------------------------------

def notificar(cfg: Config, titulo: str, mensaje: str, *,
              url: str | None = None, prioridad: int = 3,
              tags: list[str] | None = None) -> None:
    """Envía una notificación a ntfy.

    Se usa el formato JSON en lugar de cabeceras HTTP porque las cabeceras
    no admiten acentos ni emojis de forma fiable.
    """
    cuerpo = {
        "topic": cfg.ntfy_topic,
        "title": titulo,
        "message": mensaje,
        "priority": prioridad,
        "tags": tags or [],
    }
    if url:
        cuerpo["click"] = url  # al tocar la notificación abre la actividad

    resp = requests.post(cfg.ntfy_url, json=cuerpo, timeout=10)
    resp.raise_for_status()


def tiempo_restante(segundos: int) -> str:
    horas = segundos // 3600
    if horas >= 24:
        return f"{horas // 24} día(s)"
    if horas >= 1:
        return f"{horas} h"
    return f"{max(segundos // 60, 1)} min"


def describir(cfg: Config, evento: dict, restante: int) -> str:
    curso = evento.get("course", {}).get("fullname", "Curso desconocido")
    fecha = datetime.fromtimestamp(evento["timesort"], cfg.zona)
    return f"{curso}\nEntrega: {fecha:%d/%m/%Y %H:%M} (faltan {tiempo_restante(restante)})"


# --------------------------------------------------------------------------
# Estado (la "libreta" de avisos ya enviados)
# --------------------------------------------------------------------------

def cargar_estado() -> set[str] | None:
    """Devuelve las claves ya avisadas, o None si es la primera ejecución."""
    if not ESTADO_PATH.exists():
        return None
    return set(json.loads(ESTADO_PATH.read_text(encoding="utf-8")))


def guardar_estado(claves: set[str]) -> None:
    """Escritura atómica: si el proceso se interrumpe, el archivo no queda a medias."""
    temporal = ESTADO_PATH.with_suffix(".tmp")
    temporal.write_text(json.dumps(sorted(claves), indent=2), encoding="utf-8")
    temporal.replace(ESTADO_PATH)


def claves_de(evento_id: int, horas: list[int]) -> set[str]:
    """Claves de la libreta para un evento: su aviso de 'nueva' y sus recordatorios."""
    return {f"nueva:{evento_id}"} | {f"recordatorio:{evento_id}:{h}" for h in horas}


# --------------------------------------------------------------------------
# Lógica principal
# --------------------------------------------------------------------------

def horas_alcanzadas(cfg: Config, restante: int) -> list[int]:
    """Umbrales de recordatorio que ya se cruzaron (p. ej. faltan 5 h -> [24])."""
    return [h for h in cfg.horas_recordatorio if restante <= h * 3600]


def primera_ejecucion(cfg: Config, eventos: list[dict], ahora: int) -> set[str]:
    """Evita una avalancha de avisos: manda un solo resumen y marca todo como visto."""
    if eventos:
        proximo = min(eventos, key=lambda e: e["timesort"])
        mensaje = (f"{len(eventos)} actividad(es) pendiente(s).\n"
                   f"La más próxima: {proximo['name']}\n"
                   f"{describir(cfg, proximo, proximo['timesort'] - ahora)}")
    else:
        mensaje = "No tienes actividades pendientes por ahora."
    notificar(cfg, "✅ Avisos de Moodle activados", mensaje, tags=["white_check_mark"])

    estado: set[str] = set()
    for ev in eventos:
        alcanzadas = horas_alcanzadas(cfg, ev["timesort"] - ahora)
        estado |= claves_de(ev["id"], alcanzadas)
    return estado


def procesar(cfg: Config, eventos: list[dict], avisados: set[str], ahora: int) -> set[str]:
    """Envía los avisos que falten y devuelve el nuevo estado.

    El estado solo conserva claves de actividades que siguen pendientes,
    así el archivo no crece para siempre.
    """
    estado: set[str] = set()

    for ev in eventos:
        eid = ev["id"]
        restante = ev["timesort"] - ahora
        alcanzadas = horas_alcanzadas(cfg, restante)
        previas = avisados & claves_de(eid, cfg.horas_recordatorio)
        a_marcar = claves_de(eid, alcanzadas)

        try:
            if f"nueva:{eid}" not in avisados:
                notificar(cfg, f"🆕 {ev['name']}", describir(cfg, ev, restante),
                          url=ev.get("url"), tags=["books"])
            elif alcanzadas and f"recordatorio:{eid}:{min(alcanzadas)}" not in avisados:
                notificar(cfg, f"⏰ {ev['name']}", describir(cfg, ev, restante),
                          url=ev.get("url"), prioridad=4, tags=["alarm_clock"])
            else:
                a_marcar = set()
        except requests.RequestException as e:
            # Si falla el envío, no se marca: se reintentará en la próxima ejecución.
            log.warning("No se pudo notificar '%s': %s", ev["name"], e)
            a_marcar = set()

        estado |= previas | a_marcar

    return estado


def alertar_error(cfg: Config, detalle: str) -> None:
    """Avisa por ntfy que Moodle está fallando, una sola vez por incidente.

    La marca CLAVE_ALERTA se borra sola en la siguiente ejecución exitosa,
    porque `procesar` reconstruye el estado solo con actividades pendientes.
    """
    avisados = cargar_estado()
    if avisados is not None and CLAVE_ALERTA in avisados:
        return  # ya se avisó de este incidente

    try:
        notificar(
            cfg,
            "⚠️ Avisos de Moodle detenidos",
            f"Moodle respondió: {detalle}\n"
            "Si dice 'invalidtoken', genera uno nuevo con obtener_token.py "
            "y actualiza el secret MOODLE_TOKEN.",
            prioridad=4,
            tags=["warning"],
        )
    except requests.RequestException as e:
        log.warning("Tampoco se pudo enviar la alerta: %s", e)
        return

    if avisados is not None:
        guardar_estado(avisados | {CLAVE_ALERTA})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = cargar_config()
    ahora = int(time.time())

    try:
        eventos = [e for e in obtener_eventos(cfg, ahora) if es_pendiente(e)]
        avisados = cargar_estado()
        if avisados is None:
            estado = primera_ejecucion(cfg, eventos, ahora)
        else:
            estado = procesar(cfg, eventos, avisados, ahora)
    except MoodleError as e:
        log.error("Moodle devolvió un error: %s", e)
        alertar_error(cfg, str(e))
        return 1
    except requests.RequestException as e:
        log.error("Error de red: %s", e)
        return 1

    guardar_estado(estado)
    log.info("Listo: %d actividad(es) pendiente(s) revisada(s).", len(eventos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
