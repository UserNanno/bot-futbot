import os, re, requests
from typing import Text, Any, Dict, List
from datetime import datetime
import dateparser
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
from rasa_sdk.forms import FormValidationAction
from dotenv import load_dotenv

# Cargar .env si existe
load_dotenv()

API_BASE = os.getenv("FUTBOT_API_BASE", "http://localhost:3000/api")
TZ = "America/Lima"
REQ_TIMEOUT = 10

try:
    from zoneinfo import ZoneInfo
    LIMA_TZ = ZoneInfo(TZ)
except Exception:
    try:
        from pytz import timezone as _tz
        LIMA_TZ = _tz(TZ)
    except Exception:
        LIMA_TZ = None

def _date_ddmmyyyy(text: str) -> str:
    if not text:
        return ""
    dt = dateparser.parse(
        text,
        settings={"PREFER_DATES_FROM": "future", "TIMEZONE": TZ},
        languages=["es"],
    )
    return dt.strftime("%d/%m/%Y") if dt else ""

def _time_hmma(text: str) -> str:
    if not text:
        return ""
    dt = dateparser.parse(text, settings={"TIMEZONE": TZ}, languages=["es"])
    try:
        # Linux/Mac: %-I, Windows: usar %I
        return dt.strftime("%-I:%M %p").lower() if dt else ""
    except Exception:
        return dt.strftime("%I:%M %p").lstrip("0").lower() if dt else ""

def _hoy_ddmmyyyy() -> str:
    if LIMA_TZ:
        return datetime.now(LIMA_TZ).strftime("%d/%m/%Y")
    return datetime.now().strftime("%d/%m/%Y")

class ActionSetUsuarioFromSender(Action):
    """Setea el slot usuario_id desde metadata o sender_id (p.ej. 'user-123')."""

    def name(self) -> Text:
        return "action_set_usuario_from_sender"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict):
        uid = None

        # 1) metadata del canal REST ( rest: store_metadata: true)
        try:
            meta = tracker.latest_message.metadata or {}
            uid = meta.get("usuarioId") or meta.get("user_id")
        except Exception:
            uid = None

        # 2) extraer del sender_id terminando en dígitos (user-123, 123, u_45)
        if not uid and tracker.sender_id:
            m = re.search(r"(\d+)$", str(tracker.sender_id))
            if m:
                uid = m.group(1)

        if uid:
            return [SlotSet("usuario_id", str(uid))]
        return []

class ValidateConsultaForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_consulta_form"

    def validate_fecha(self, value, dispatcher, tracker, domain):
        # Si NO hay fecha pero SÍ hay hora → asumir HOY (America/Lima)
        if (not value) and tracker.get_slot("hora"):
            return {"fecha": _hoy_ddmmyyyy()}

        p = _date_ddmmyyyy(value)
        if p:
            return {"fecha": p}
        dispatcher.utter_message(text="Fecha ej. 25/10/2025")
        return {"fecha": None}

    def validate_hora(self, value, dispatcher, tracker, domain):
        p = _time_hmma(value)
        if p:
            # Si hora OK y no hay fecha aún → también fijamos HOY acá
            if not tracker.get_slot("fecha"):
                return {"hora": p, "fecha": _hoy_ddmmyyyy()}
            return {"hora": p}
        dispatcher.utter_message(text="Hora ej. 8:00 pm")
        return {"hora": None}

class ValidateReservaForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_reserva_form"

    def validate_usuario_id(self, value, dispatcher, tracker, domain):
        try:
            return {"usuario_id": str(int(str(value).strip()))}
        except Exception:
            dispatcher.utter_message(text="usuarioId debe ser numérico (ej. 1)")
            return {"usuario_id": None}

    def validate_fecha(self, value, dispatcher, tracker, domain):
        # Para reservas directas: si solo te dan hora, asume HOY
        if (not value) and tracker.get_slot("hora"):
            return {"fecha": _hoy_ddmmyyyy()}
        p = _date_ddmmyyyy(value)
        if p:
            return {"fecha": p}
        dispatcher.utter_message(text="Fecha ej. 25/10/2025")
        return {"fecha": None}

    def validate_hora(self, value, dispatcher, tracker, domain):
        p = _time_hmma(value)
        if p:
            if not tracker.get_slot("fecha"):
                return {"hora": p, "fecha": _hoy_ddmmyyyy()}
            return {"hora": p}
        dispatcher.utter_message(text="Hora ej. 8:00 pm")
        return {"hora": None}

class ActionConsultarDisponibilidad(Action):
    def name(self) -> Text:
        return "action_consultar_disponibilidad"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        fecha = tracker.get_slot("fecha")
        hora = tracker.get_slot("hora")

        if not fecha or not hora:
            dispatcher.utter_message(text="Necesito la fecha y la hora para consultar.")
            return []

        payload = {"fecha": fecha, "hora": hora}

        try:
            r = requests.post(f"{API_BASE}/reservas/disponibilidad", json=payload, timeout=REQ_TIMEOUT)
            try:
                data = r.json()
            except Exception:
                dispatcher.utter_message(text=f"Error interpretando la respuesta del servidor (HTTP {r.status_code}).")
                return []

            if isinstance(data, dict) and "disponible" in data:
                if data["disponible"]:
                    dispatcher.utter_message(
                        text=f"{data.get('mensaje', 'Horario disponible')} para {fecha} a las {hora}. ¿Deseas reservar?"
                    )
                    # Guardar fecha/hora para el “sí”
                    return [SlotSet("fecha", fecha), SlotSet("hora", hora)]
                else:
                    dispatcher.utter_message(
                        text=f"{data.get('mensaje', 'No disponible')} para {fecha} a las {hora}."
                    )
                    return []
            else:
                dispatcher.utter_message(text=f"Respuesta del servidor: {data}")
                return []

        except Exception as e:
            print("Error en action_consultar_disponibilidad:", e)
            dispatcher.utter_message(text="No pude consultar la disponibilidad en este momento.")
            return []

class ActionCrearReserva(Action):
    def name(self) -> Text:
        return "action_crear_reserva"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        fecha = tracker.get_slot("fecha")
        hora = tracker.get_slot("hora")
        usuario_id = tracker.get_slot("usuario_id")

        if not usuario_id:
            dispatcher.utter_message(
                text="No tengo tu usuario_id. Si usas REST, envía sender='user-<id>' o metadata {'usuarioId': <id>}."
            )
            return []

        if not fecha or not hora:
            dispatcher.utter_message(text="No tengo registrada la fecha u hora. Primero consulta la disponibilidad.")
            return []

        try:
            payload = {"usuarioId": usuario_id, "fecha_reserva": fecha, "hora_reserva": hora}
            r = requests.post(f"{API_BASE}/reservas", json=payload, timeout=REQ_TIMEOUT)

            if r.status_code == 201:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                reserva_id = data.get("id")
                dispatcher.utter_message(
                    text=f"Reserva creada (ID: {reserva_id}) para {fecha} a las {hora}."
                )
                return []

            try:
                data = r.json()
                mensaje = data.get("mensaje", f"Error {r.status_code}")
            except Exception:
                mensaje = f"Error {r.status_code} del servidor"
            dispatcher.utter_message(text=f"❌ {mensaje}")
            return []

        except Exception as e:
            print("Error en action_crear_reserva:", e)
            dispatcher.utter_message(text="❌ No pude crear la reserva. Puede que ese horario ya no esté libre.")
            return []
