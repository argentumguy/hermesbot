#enviar_admin.py

import os
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# este script permite enviar un mensaje de prueba desde fuera del bot.
# sirve para verificar que el endpoint de administración está funcionando.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
if not ADMIN_TOKEN:
    print("❌ Falta ADMIN_TOKEN en .env")
    exit(1)

URL = input("URL pública del bot (ej: https://xxxx.ngrok-free.app): ").strip() #es la url que te da ngrok, o la url de tu servidor si lo tenes en un hosting
numero = input("Número (formato internacional, ej: 5493425232830): ").strip() #el número al que queres enviar el mensaje, con código de país, de área y sin signos ni espacios
texto = input("Mensaje: ").strip()

response = requests.post(
    f"{URL}/admin/enviar",
    headers={"X-Admin-Token": ADMIN_TOKEN},
    json={"numero": numero, "texto": texto}
)

print("Respuesta:", response.status_code, response.text)