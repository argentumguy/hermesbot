import json
import logging
import os

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

logger = logging.getLogger(__name__)


def obtener_cliente_redis():
    """devuelve el cliente de redis si esta disponible, o none si no hay servicio activo."""
    if redis is None:
        logger.info("redis no esta instalado; se usa la memoria local del proceso.")
        return None

    url_redis = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        cliente_redis = redis.Redis.from_url(url_redis, decode_responses=True)
        cliente_redis.ping()
        return cliente_redis
    except Exception:
        logger.warning("no se pudo conectar con redis; se usa la memoria local del proceso.")
        return None


def construir_llave(nombre, valor):
    """arma una clave limpia para guardar estado en redis."""
    return f"bot:{nombre}:{valor}"


def guardar_sesion_usuario(usuario_id, datos, ttl=180):
    """guarda la sesion activa del usuario en redis para mantener el contexto entre mensajes."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return False

    llave_sesion = construir_llave("sesion", usuario_id)
    try:
        cliente_redis.setex(llave_sesion, ttl, json.dumps(datos, default=str))
        return True
    except Exception:
        logger.exception("no se pudo guardar la sesion del usuario en redis")
        return False


def obtener_sesion_usuario(usuario_id):
    """lee la sesion guardada del usuario en redis, si existe."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return None

    llave_sesion = construir_llave("sesion", usuario_id)
    try:
        dato = cliente_redis.get(llave_sesion)
        if dato is None:
            return None
        return json.loads(dato)
    except Exception:
        logger.exception("no se pudo leer la sesion del usuario desde redis")
        return None


def borrar_sesion_usuario(usuario_id):
    """elimina la sesion del usuario para limpiar el contexto despues de una accion."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return False

    llave_sesion = construir_llave("sesion", usuario_id)
    try:
        cliente_redis.delete(llave_sesion)
        return True
    except Exception:
        logger.exception("no se pudo borrar la sesion del usuario en redis")
        return False


def guardar_memoria_cortesia(usuario_id, datos, ttl=180):
    """guarda la memoria de cortesía para responder si el usuario agradece despues de una ayuda."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return False

    llave_memoria = construir_llave("cortesia", usuario_id)
    try:
        cliente_redis.setex(llave_memoria, ttl, json.dumps(datos, default=str))
        return True
    except Exception:
        logger.exception("no se pudo guardar la memoria de cortesía en redis")
        return False


def obtener_memoria_cortesia(usuario_id):
    """lee la memoria de cortesía del usuario en redis."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return None

    llave_memoria = construir_llave("cortesia", usuario_id)
    try:
        dato = cliente_redis.get(llave_memoria)
        if dato is None:
            return None
        return json.loads(dato)
    except Exception:
        logger.exception("no se pudo leer la memoria de cortesía desde redis")
        return None


def borrar_memoria_cortesia(usuario_id):
    """elimina la memoria de cortesía cuando ya no sirve para la conversacion."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return False

    llave_memoria = construir_llave("cortesia", usuario_id)
    try:
        cliente_redis.delete(llave_memoria)
        return True
    except Exception:
        logger.exception("no se pudo borrar la memoria de cortesía en redis")
        return False


def guardar_cache_bd(datos, ttl=600):
    """guarda la base de datos de recursos en redis para reducir llamadas repetidas a google sheets."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return False

    llave_cache = "bot:cache:base_datos"
    try:
        cliente_redis.setex(llave_cache, ttl, json.dumps(datos, default=str))
        return True
    except Exception:
        logger.exception("no se pudo guardar el cache de la base de datos en redis")
        return False


def obtener_cache_bd():
    """lee la base de datos guardada en redis para reutilizarla sin volver a consultar sheets."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return None

    llave_cache = "bot:cache:base_datos"
    try:
        dato = cliente_redis.get(llave_cache)
        if dato is None:
            return None
        return json.loads(dato)
    except Exception:
        logger.exception("no se pudo leer el cache de la base de datos desde redis")
        return None


def permitir_peticion(usuario_id, limite=20, ventana_segundos=60):
    """permite limitar la cantidad de mensajes por usuario para evitar abuso o spam."""
    cliente_redis = obtener_cliente_redis()
    if cliente_redis is None:
        return True

    llave_peticion = construir_llave("rate_limit", usuario_id)
    try:
        conteo_actual = cliente_redis.get(llave_peticion)
        if conteo_actual is None:
            cliente_redis.setex(llave_peticion, ventana_segundos, 1)
            return True

        conteo_actual = int(conteo_actual)
        if conteo_actual >= limite:
            return False

        cliente_redis.incr(llave_peticion)
        return True
    except Exception:
        logger.exception("no se pudo validar el limite de peticiones por usuario")
        return True
