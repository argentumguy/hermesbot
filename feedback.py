# feedback.py
import os
import gspread
from datetime import datetime

# esta función guarda el comentario del usuario en la hoja de feedback de Google Sheets.
# sirve para registrar sugerencias, errores o mensajes de ayuda para mejorar el bot.
def guardar_feedback(usuario_id, tipo, mensaje, contacto=""):
    """
    guarda el feedback en la hoja 'Feedback' de Google Sheets
    devuelve True si se guardó correctamente
    
    """
    try:
        CREDENCIALES = os.getenv("GOOGLE_CREDENTIALS_PATH", "credenciales.json")
        cliente = gspread.service_account(filename=CREDENCIALES)
        documento = cliente.open_by_key(os.getenv("GOOGLE_SHEETS_ID"))
        hoja = documento.worksheet("Feedback")

        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fila = [fecha, str(usuario_id), tipo, mensaje, contacto]
        hoja.append_row(fila)
        return True
    except Exception as e:
        print(f"Error guardando feedback: {e}")
        return False