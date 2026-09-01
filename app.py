# aplicacion.py

import os
import hmac
import hashlib
import logging
import threading
import time
import requests
import sys

from flask import Flask, request
from dotenv import load_dotenv, find_dotenv

from conexion import hermes_bd
from motor import procesar_mensaje
from control_acceso import verificar_acceso_usuario

# carga variables de entorno para que la aplicación pueda autenticarse con
# WhatsApp, Google Sheets y tokens de administración.
load_dotenv(find_dotenv())

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# configurar logging para todo el proyecto
logging.basicConfig(
    level=logging.INFO,  # nivel mínimo: INFO, WARNING, ERROR, CRITICAL
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("hermes_bot.log"),  # guarda en este archivo
        logging.StreamHandler()                # también muestra en consola (opcional). ensucia la salida si se usa ngrok, pero útil para debug
    ]
)

logger = logging.getLogger(__name__)

# ahora todos los módulos que usen logger = logging.getLogger(__name__)
# van a escribir en el archivo y en consola


# la app de Flask recibe los webhooks de WhatsApp y sirve como backend del bot.
# aquí se define la interfaz web que Meta y el servidor de hosting llaman.
app = Flask(__name__)

META_TOKEN = os.getenv("META_TOKEN")
META_PHONE_ID = os.getenv("META_PHONE_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_API_URL = f"https://graph.facebook.com/v20.0/{META_PHONE_ID}/messages" if META_PHONE_ID else None
PUBLIC_DEMO_MODE = not all([META_TOKEN, META_PHONE_ID, META_VERIFY_TOKEN, META_APP_SECRET, ADMIN_TOKEN])

if PUBLIC_DEMO_MODE:
    logger.warning("Modo público/demo activo: faltan variables privadas. La app queda deshabilitada para no depender de secretos ni bases reales.")
    bd = {}
else:
    # ---------------- BASE DE DATOS -----------------
    logger.info("Cargando base de datos...")
    bd = hermes_bd()
    if bd is None:
        logger.error("No se pudo cargar la base de datos. Saliendo.")
        sys.exit(1)
    logger.info("Base de datos cargada.")

# ---------------- FUNCIONES AUXILIARES ----------------
# esta función valida que el webhook venga realmente de Meta y no de un tercero.
# sin esa verificación, cualquier persona podría simular mensajes.
def verificar_firma(request):
    """
    verifica la firma del webhook de Meta usando el secreto de la aplicación (META_APP_SECRET configurado en el .env | Facebook Developer->app->conf app->inf basica)
    
    """
    firma_recibida = request.headers.get("X-Hub-Signature-256", "")
    if not firma_recibida:
        return False

    # construir la firma esperada
    cuerpo = request.get_data()
    firma_esperada = "sha256=" + hmac.new(
        META_APP_SECRET.encode("utf-8"),
        cuerpo,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(firma_recibida, firma_esperada)

def enviar_mensaje_meta(to, texto):
    """
    envía un mensaje de whatsApp usando la api de Meta
    
    """
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": texto}
    }

    try:
        response = requests.post(META_API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error("Error al enviar mensaje: %s %s", response.status_code, response.text)
    except requests.exceptions.RequestException as e:
        logger.error("Excepción al enviar mensaje: %s", e)

def recargar_bd_periodicamente(intervalo_segundos=300):
    """
    recarga la base de datos en segundo plano cada X segundos (300 segundos = 5 minutos por defecto)
    
    """
    global bd
    while True:
        time.sleep(intervalo_segundos)
        try:
            logger.info("Recargando base de datos...")
            nueva_bd = hermes_bd(recargar=True)
            if nueva_bd is not None:
                bd = nueva_bd
                logger.info("Base de datos actualizada.")
        except Exception as e:
            logger.exception("Error al recargar la base de datos")

# ---------------- RUTAS ----------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """
    Verificación del webhook para Meta.
   
     """
    if PUBLIC_DEMO_MODE:
        return "Versión pública: la app está deshabilitada sin configuración privada.", 200

    token_sent = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if META_VERIFY_TOKEN and token_sent == META_VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

# esta ruta procesa cada mensaje que entra por WhatsApp y delega la respuesta
# al motor principal de lógica del bot.
@app.route("/webhook", methods=["POST"])
def webhook():
    """
    recepción de mensajes de WhatsApp
    
    """
    if PUBLIC_DEMO_MODE:
        return "Versión pública: sin secretos ni servicios reales configurados.", 200

    # verificar firma 
    if not verificar_firma(request):
        logger.warning("Firma inválida en webhook")
        return "Firma inválida", 403

    data = request.get_json()
    if data is None:
        return "Sin datos", 400

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    value = change.get("value", {})
                    for message in value.get("messages", []):
                        from_number = message.get("from")
                        text = message.get("text", {}).get("body", "")

                        if not from_number:
                            continue

                        if not verificar_acceso_usuario(from_number, bd):
                            logger.info("Se ignora el mensaje de %s porque no está verificado.", from_number)
                            continue

                        # verifica que sea un mensaje de texto
                        if message.get("type") != "text":
                            enviar_mensaje_meta(from_number, "Lo siento, solo puedo procesar mensajes de texto.")
                            continue

                        if text:
                            logger.info("Mensaje de %s: %s", from_number, text)
                            respuesta = procesar_mensaje(text, bd, usuario_id=from_number)
                            enviar_mensaje_meta(from_number, respuesta)
    except Exception as e:
        logger.exception("Error procesando webhook")

    return "OK", 200


@app.route("/admin/enviar", methods=["POST"])   ## manda mensaje manual desde enviar_admin.py
def admin_enviar():
    """ envía un mensaje a un número desde un panel externo """
    if PUBLIC_DEMO_MODE:
        return "Versión pública: sin acceso a servicios privados.", 403

    # verifica token de administración
    token_recibido = request.headers.get("X-Admin-Token", "")
    if token_recibido != ADMIN_TOKEN:
        return "No autorizado", 403

    data = request.get_json()
    if not data:
        return "Sin datos", 400

    numero = data.get("numero")
    texto = data.get("texto")

    if not numero or not texto:
        return "Faltan numero o texto", 400

    try:
        enviar_mensaje_meta(numero, texto)
        return "Mensaje enviado", 200
    except Exception as e:
        logger.error("Error enviando mensaje admin: %s", e)
        return "Error al enviar", 500


# ---------------- INICIO ----------------
# inicia el hilo de recarga en cualquier entorno que importe el módulo:
# gunicorn / azure / local dev. No va dentro de __main__.
if not PUBLIC_DEMO_MODE:
    hilo_recarga = threading.Thread(target=recargar_bd_periodicamente, args=(900,), daemon=True)
    hilo_recarga.start()

if __name__ == "__main__":
    # solo para pruebas locales. en aure/gunicorn la variable PORT la define el host
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", debug=False, port=port)


# iniciar ngrok solo en desarrollo local, no en Azure/Gunicorn
# try:
#     from pyngrok import ngrok
#     ngrok.set_auth_token(os.getenv("NGROK_AUTHTOKEN"))
#     public_url = ngrok.connect(5000)
#     logger.info("🔗 URL pública: %s", public_url)
# except Exception as e:
#     logger.warning("No se pudo iniciar ngrok: %s", e)