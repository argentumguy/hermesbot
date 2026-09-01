# conexion.py
import json
import os
import logging
from dotenv import load_dotenv, find_dotenv
import gspread

from redis_storage import obtener_cache_bd, guardar_cache_bd
from utils import construir_indices

# configurar logging
logger = logging.getLogger(__name__)

# este cache guarda la base de datos cargada para no volver a consultar
# Google Sheets cada vez que un usuario envía un mensaje.
_bd_cache = None

# esta función centraliza la carga de la base de datos desde Google Sheets.
# devuelve una estructura lista para buscar materias, recursos, horarios y sinónimos.
def hermes_bd(recargar=False):
    """
    se conecta a Sheets y descarga las pestañas de la base de datos,
    si la base ya fue cargada y no se pide recargar, devuelve la caché
    
    """
    global _bd_cache

    if _bd_cache is not None and not recargar:
        logger.info("Usando base de datos cacheada en memoria.")
        return _bd_cache

    base_cache_redis = obtener_cache_bd() if not recargar else None
    if base_cache_redis is not None and not recargar:
        logger.info("Usando base de datos cacheada en redis.")
        _bd_cache = base_cache_redis
        return _bd_cache

    logger.info("Iniciando conexión con Google Sheets...")

    # carga las variables de entorno desde el archivo .env, así el bot no queda
    # hardcodeado ni depende de valores manuales dentro del código.
    load_dotenv(find_dotenv())

    try:
        # 1 autenticación con credencial JSON
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            info_clave = json.loads(creds_json)
            cliente = gspread.service_account_from_dict(info_clave)
        else:
            path_archivo = os.getenv("GOOGLE_CREDENTIALS_PATH", "credenciales.json")
            if not os.path.isfile(path_archivo):
                logger.error("No se encontraron credenciales de Google.")
                return None
            cliente = gspread.service_account(filename=path_archivo)
        # 2 conexión al documento
        ID_DOCUMENTO = os.getenv("GOOGLE_SHEETS_ID")
        if not ID_DOCUMENTO:
            logger.error("Falta la variable GOOGLE_SHEETS_ID en el archivo .env")
            return None

        documento = cliente.open_by_key(ID_DOCUMENTO)

        # 3 acceso a las pestañas exactas por nombre
        nombres_hojas_obligatorias = ["Materias", "Recursos", "Sinonimos", "Horarios"]
        nombres_hojas_opcionales = ["Usuarios", "CodigosInvitacion", "Configuracion"]
        hojas = {}
        for nombre in nombres_hojas_obligatorias + nombres_hojas_opcionales:
            try:
                hojas[nombre] = documento.worksheet(nombre)
            except gspread.exceptions.WorksheetNotFound:
                if nombre in nombres_hojas_obligatorias:
                    logger.error("No se encontró la pestaña '%s' en el documento.", nombre)
                    return None
                logger.warning("La pestaña opcional '%s' no existe todavía. Se dejará vacía.", nombre)

        # 4 descarga de datos
        bd = {
            "materias": hojas["Materias"].get_all_records(),
            "recursos": hojas["Recursos"].get_all_records(),
            "sinonimos": hojas["Sinonimos"].get_all_records(),
            "horarios": hojas["Horarios"].get_all_records(),
            "usuarios": hojas.get("Usuarios", []).get_all_records() if hojas.get("Usuarios") else [],
            "codigos_invitacion": hojas.get("CodigosInvitacion", []).get_all_records() if hojas.get("CodigosInvitacion") else [],
            "configuracion": hojas.get("Configuracion", []).get_all_records() if hojas.get("Configuracion") else []
        }

        # filtrar filas vacías (todas las celdas sin contenido)
        for clave in bd:
            bd[clave] = [
                fila for fila in bd[clave]
                if any(str(valor).strip() for valor in fila.values())
            ]

        # validar columnas obligatorias
        columnas_requeridas = {
            "materias": ["id_materia", "nombre_normalizado"],
            "recursos": ["id_materia", "tipo_recurso", "link"],
            "sinonimos": ["alias", "id_materia"],
            "horarios": ["fecha", "id_materia"]
        }
        for clave, columnas in columnas_requeridas.items():
            if bd[clave]:
                encabezados = list(bd[clave][0].keys())
                for col in columnas:
                    if col not in encabezados:
                        logger.error("La hoja '%s' no tiene la columna '%s'.", clave, col)
                        return None

        # normalizar iDs a string
        for materia in bd['materias']:
            materia['id_materia'] = str(materia.get('id_materia', '')).strip()
        for recurso in bd['recursos']:
            recurso['id_materia'] = str(recurso.get('id_materia', '')).strip()
            recurso['id_recurso'] = str(recurso.get('id_recurso', '')).strip()
        for sinonimo in bd['sinonimos']:
            sinonimo['id_materia'] = str(sinonimo.get('id_materia', '')).strip()
        for horario in bd['horarios']:
            horario['id_materia'] = str(horario.get('id_materia', '')).strip()

        # construir índices de búsqueda
        construir_indices(bd)

        # guarda en caché
        _bd_cache = bd
        guardar_cache_bd(bd)

        logger.info("Base de datos cargada exitosamente.")
        logger.info("📊 Resumen: %d materias, %d recursos, %d sinónimos, %d horarios.",
                    len(bd['materias']), len(bd['recursos']),
                    len(bd['sinonimos']), len(bd['horarios']))

        return bd

    except Exception as error:
        logger.exception("❌ Ocurrió un error al conectar con Google Sheets")
        return None


if __name__ == "__main__":
    # prueba de carga
    base_datos = hermes_bd()
    if base_datos:
        print("Carga exitosa.")
        print("Ejemplo de materias:", base_datos["materias"][:2])
    else:
        print("No se pudo cargar la base de datos.")