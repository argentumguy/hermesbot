import random
import re
from datetime import datetime, timedelta


def normalizar_numero(numero):
    """normaliza un numero de whatsapp para que pueda compararse de forma segura."""
    if numero is None:
        return ""
    texto = str(numero).strip()
    texto = re.sub(r"\D", "", texto)
    return texto


def generar_codigo_invitacion():
    """genera un codigo unico con formato legible para ser compartido por un usuario autorizado."""
    grupo = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    parte = "".join(random.choice(grupo) for _ in range(6))
    return f"HERMES-{parte}"


def validar_codigo_invitacion(codigo, numero_nuevo, registros):
    """valida si un codigo de invitacion es valido para un numero nuevo."""
    codigo_limpio = str(codigo or "").strip().upper()
    numero_limpio = normalizar_numero(numero_nuevo)

    if not codigo_limpio:
        return False, "no se envio un codigo."

    if not numero_limpio:
        return False, "no se pudo identificar el numero."

    for registro in registros:
        if str(registro.get("codigo", "")).strip().upper() != codigo_limpio:
            continue

        estado = str(registro.get("estado", "")).lower()
        if estado in {"usado", "expirado", "cancelado", "inactivo"}:
            return False, f"este codigo ya no es valido ({estado})."

        fecha_expiracion = str(registro.get("fecha_expiracion", ""))
        if fecha_expiracion:
            try:
                expira = datetime.strptime(fecha_expiracion, "%Y-%m-%d")
                if datetime.now() > expira:
                    return False, "este codigo expiro."
            except ValueError:
                pass

        if str(registro.get("usado_por", "")) == numero_limpio:
            return False, "este codigo ya fue usado por ese numero."

        return True, "codigo valido."

    return False, "codigo no encontrado."


def construir_registro_codigo(codigo, creado_por, horas_vigencia=168):
    """crea un registro de codigo para una hoja de google sheets."""
    fecha_hoy = datetime.now()
    fecha_expiracion = (fecha_hoy + timedelta(hours=horas_vigencia)).strftime("%Y-%m-%d")
    return {
        "codigo": codigo,
        "creado_por": normalizar_numero(creado_por),
        "estado": "activo",
        "fecha_creacion": fecha_hoy.strftime("%Y-%m-%d"),
        "fecha_expiracion": fecha_expiracion,
        "usado_por": "",
        "fecha_uso": "",
    }


def nombre_de_hoja_google(nombre):
    """devuelve el nombre normalizado de una hoja para evitar errores de escritura."""
    return str(nombre).strip()


def usuario_esta_verificado(numero, bd, permitir_sin_configuracion=True):
    """devuelve True si el numero figura como verificado en la base de datos del bot."""
    numero_limpio = normalizar_numero(numero)
    if not numero_limpio:
        return False

    if not isinstance(bd, dict):
        return permitir_sin_configuracion

    usuarios = bd.get("usuarios") or []
    if not usuarios and not bd.get("codigos_invitacion") and not bd.get("configuracion"):
        return permitir_sin_configuracion

    for fila in usuarios:
        if normalizar_numero(fila.get("numero", "")) != numero_limpio:
            continue
        estado = str(fila.get("estado", "")).lower()
        if estado in {"verificado", "activo", "autorizado", "admin"}:
            return True
        return False

    return False


def verificar_acceso_usuario(numero, bd, permitir_sin_configuracion=True):
    """gate de seguridad para webhooks; ignora silenciosamente a los no verificados."""
    if not numero:
        return False
    return usuario_esta_verificado(numero, bd, permitir_sin_configuracion=permitir_sin_configuracion)


def registrar_verificacion_por_codigo(codigo, numero_nuevo, bd):
    """registra un usuario nuevo como verificado cuando usa un codigo valido."""
    registros = bd.get("codigos_invitacion") or []
    ok, mensaje = validar_codigo_invitacion(codigo, numero_nuevo, registros)
    if not ok:
        return False, mensaje

    for registro in registros:
        if str(registro.get("codigo", "")).strip().upper() == str(codigo).strip().upper():
            registro["estado"] = "usado"
            registro["usado_por"] = normalizar_numero(numero_nuevo)
            registro["fecha_uso"] = datetime.now().strftime("%Y-%m-%d")
            break

    usuarios = bd.setdefault("usuarios", [])
    usuarios.append({
        "numero": normalizar_numero(numero_nuevo),
        "estado": "verificado",
        "invitado_por": "",
        "codigo_usado": str(codigo).strip().upper(),
        "fecha_alta": datetime.now().strftime("%Y-%m-%d"),
        "cantidad_invitados": 0,
    })

    return True, "usuario verificado correctamente."
