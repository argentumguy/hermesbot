# motor.py

import time
import copy
import logging
from collections import defaultdict
from datetime import date, timedelta

from redis_storage import (
    guardar_sesion_usuario,
    obtener_sesion_usuario,
    borrar_sesion_usuario,
    guardar_memoria_cortesia,
    obtener_memoria_cortesia,
    borrar_memoria_cortesia,
)
from utils import (
    limpiar_texto,
    coinciden_palabras,
    obtener_nombre_profesor,
    detectar_tipo_recurso,
    construir_indices
)

# configura logging para registrar errores y trazas de la lógica del bot.
logger = logging.getLogger(__name__)

###############################
# MEMORIA / ESTADO DE USUARIO #
###############################

# esta estructura guarda la sesión activa de cada usuario para mantener conversaciones
# en curso, como cuando se le pregunta una materia o un profesor luego de un primer mensaje.
sesiones_usuarios = {}
TIEMPO_MAX_SESION = 180  # 3 minutos

# memoria de cortesía para responder agradecimientos repetidos sin confundirse con una consulta real.

memoria_cortesia = {}

# estas palabras ayudan a identificar si el usuario está haciendo una consulta real
# o si, por el contrario, solo está agradeciendo por una respuesta anterior.
PALABRAS_TIPO_RECURSO = {
    "aula", "virtual", "grupo", "whatsapp", "wpp", "wsp", "apunte", "apuntes",
    "programa", "programas", "clase", "cursar", "curso", "recurso", "recursos",
    "link", "enlace", "material", "materia"
}
PALABRAS_BUSQUEDA = PALABRAS_TIPO_RECURSO | {"horario", "horarios", "hora", "horas", "bedelia", "donde"}

# al marcar una consulta como exitosa, el bot puede responder con cortesía si el usuario
# luego dice "gracias" sin hacer una nueva consulta real.
def marcar_consulta_exitosa(usuario_id):
    datos = {"ayudado": True, "gracias_count": 0, "ultimo_acceso": time.time()}
    memoria_cortesia[usuario_id] = datos
    guardar_memoria_cortesia(usuario_id, datos)

def limpiar_cortesia(usuario_id):
    memoria_cortesia.pop(usuario_id, None)
    borrar_memoria_cortesia(usuario_id)

def es_agradecimiento_puro(mensaje):
    tokens = mensaje.lower().split()
    gracias_tokens = {"gracias", "graciass", "grx", "thanks", "thank"}
    es_gracias = any(t.startswith("graci") and len(t) >= 5 for t in tokens) or any(t in gracias_tokens for t in tokens)
    if not es_gracias:
        return False
    for t in tokens:
        if t in PALABRAS_BUSQUEDA:
            return False
    return True

def guardar_sesion(usuario_id, datos):
    datos["ultimo_acceso"] = time.time()
    sesiones_usuarios[usuario_id] = datos
    guardar_sesion_usuario(usuario_id, datos)


def obtener_sesion_activa(usuario_id):
    """devuelve la sesion del usuario desde redis si existe, o la memoria local del proceso."""
    sesion_redis = obtener_sesion_usuario(usuario_id)
    if sesion_redis is not None:
        sesiones_usuarios[usuario_id] = sesion_redis
        return sesion_redis
    return sesiones_usuarios.get(usuario_id)


def limpiar_sesiones_expiradas():
    ahora = time.time()
    for uid in list(sesiones_usuarios.keys()):
        ultimo = sesiones_usuarios[uid].get("ultimo_acceso", 0)
        if ahora - ultimo > TIEMPO_MAX_SESION:
            del sesiones_usuarios[uid]
            borrar_sesion_usuario(uid)

    for uid in list(memoria_cortesia.keys()):
        ultimo = memoria_cortesia[uid].get("ultimo_acceso", 0)
        if ahora - ultimo > TIEMPO_MAX_SESION:
            del memoria_cortesia[uid]
            borrar_memoria_cortesia(uid)

#################################################
# FUNCIONES AUXILIARES                          #
#################################################

# esta función intenta resolver una materia por sinónimo exacto o por el alias
# más largo que aparece dentro del mensaje del usuario.
def buscar_por_sinonimos(mensaje, bd):
    """ busca en la hoja Sinonimos el alias más específico """
    alias_exacto = bd['indice_sinonimos'].get(mensaje)
    if alias_exacto:
        return alias_exacto['id_materia']

    mejor_id = None
    mejor_longitud = 0
    tokens_mensaje = set(mensaje.split())

    for sinonimo in bd['sinonimos']:
        alias = limpiar_texto(sinonimo.get('alias', ''))
        if not alias:
            continue
        tokens_alias = set(alias.split())
        if tokens_alias.issubset(tokens_mensaje):
            if len(alias) > mejor_longitud:
                mejor_id = sinonimo['id_materia']
                mejor_longitud = len(alias)
    return mejor_id

def encontrar_materia(mensaje, bd):
    """ busca la materia por sinónimos y nombre normalizado """
    id_por_sinonimo = buscar_por_sinonimos(mensaje, bd)
    if id_por_sinonimo:
        return id_por_sinonimo

    palabras_mensaje = set(mensaje.split())
    mejor_materia = None
    mejor_puntaje = 0

    for materia in bd['materias']:
        nombre_norm = limpiar_texto(str(materia['nombre_normalizado']))
        puntaje = 0
        for palabra in palabras_mensaje:
            if len(palabra) > 2 and palabra in nombre_norm:
                puntaje += 1
        if nombre_norm and nombre_norm in mensaje:
            puntaje += 10
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_materia = materia

    if mejor_materia and mejor_puntaje > 0:
        return mejor_materia['id_materia']
    return None

def detectar_profesor_en_recursos(mensaje, bd):
    """ busca en todos los recursos si el mensaje menciona un profesor o cátedra """
    profesor_detectado = None
    materias_asociadas = set()
    recursos_coincidentes = []

    for recurso in bd['recursos']:
        nombre_prof = obtener_nombre_profesor(recurso)
        catedra = recurso.get('catedra', '')
        if coinciden_palabras(mensaje, nombre_prof, 3) or coinciden_palabras(mensaje, catedra, 3):
            if profesor_detectado is None:
                profesor_detectado = nombre_prof
            recursos_coincidentes.append(recurso)
            materias_asociadas.add(str(recurso['id_materia']))

    return profesor_detectado, list(materias_asociadas), recursos_coincidentes

def detectar_profesor_en_lista(mensaje, recursos):
    """ busca si el mensaje menciona un profesor/cátedra dentro de una lista de recursos """
    for recurso in recursos:
        nombre_prof = obtener_nombre_profesor(recurso)
        catedra = recurso.get('catedra', '')
        if coinciden_palabras(mensaje, nombre_prof, 3) or coinciden_palabras(mensaje, catedra, 3):
            return nombre_prof
    return None

def filtrar_por_profesor(recursos, nombre_profesor):
    """ filtra una lista de recursos por el nombre del profesor/cátedra """
    if not nombre_profesor:
        return recursos
    nombre_buscado = limpiar_texto(nombre_profesor)
    resultados = []
    for recurso in recursos:
        prof_actual = limpiar_texto(obtener_nombre_profesor(recurso))
        catedra_actual = limpiar_texto(recurso.get('catedra', ''))
        if coinciden_palabras(nombre_buscado, prof_actual, 3) or coinciden_palabras(nombre_buscado, catedra_actual, 3):
            resultados.append(recurso)
    return resultados

def filtrar_por_comision_o_anio(mensaje, recursos):
    """
    si el mensaje contiene una comisión o un año exacto, filtra los recursos
    por ese valor. devuelve los recursos que coinciden o la lista original
    si no se detecta comisión/año
    
    """
    texto = limpiar_texto(mensaje)
    palabras = set(texto.split())

    # detecta comisión usando coincidencia por tokens
    comisiones_coincidentes = set()
    for r in recursos:
        comision = r.get('comision', '')
        if coinciden_comision(texto, comision):
            comisiones_coincidentes.add(limpiar_texto(comision))

    if comisiones_coincidentes:
        resultados = []
        for r in recursos:
            if limpiar_texto(r.get('comision', '')) in comisiones_coincidentes:
                resultados.append(r)
        if resultados:
            return resultados

    # detecta año exacto
    for r in recursos:
        anio = limpiar_texto(r.get('anio', ''))
        if anio and anio in texto:
            resultados = [r for r in recursos if limpiar_texto(r.get('anio', '')) == anio]
            if resultados:
                return resultados

    return recursos

def extraer_comisiones(recursos):
    """ devuelve una lista de comisiones únicas (normalizadas) """
    comisiones = set()
    for r in recursos:
        comision = limpiar_texto(r.get('comision', ''))
        if comision:
            comisiones.add(comision)
    return list(comisiones)

def coinciden_comision(mensaje, comision_recurso):
    """
    devuelve True si alguna palabra del mensaje coincide con alguna
    de las comisiones separadas por guión (ej: b1 con B1-B2)
    
    """
    if not comision_recurso:
        return False

    comision_limpia = limpiar_texto(comision_recurso)      # "b1-b2" -> "b1 b2"
    tokens_comision = set(comision_limpia.replace("-", " ").split())
    tokens_mensaje = set(mensaje.split())

    return bool(tokens_comision & tokens_mensaje)

def extraer_anios(recursos):
    """ devuelve una lista de años únicos (normalizados) """
    anios = set()
    for r in recursos:
        anio = limpiar_texto(r.get('anio', ''))
        if anio:
            anios.add(anio)
    return list(anios)

def formatear_recurso(r, incluir_materia=False):
    """ formatea la respuesta final con los datos del recurso """
    nombre_prof = obtener_nombre_profesor(r)
    catedra = r.get('catedra', '')
    comision = r.get('comision', '')
    anio = r.get('anio', '')
    tipo = r.get('tipo_recurso', '')
    descripcion = r.get('descripcion', '')
    link = r.get('link', '')

    partes = []
    if incluir_materia and 'nombre_materia' in r:
        partes.append(f"📘 Materia: {r['nombre_materia']}")
    if nombre_prof:
        partes.append(f"👨‍🏫 Profesor/Cátedra: {nombre_prof}")
    if catedra and catedra != nombre_prof:
        partes.append(f"🏛️ Cátedra: {catedra}")
    if comision:
        partes.append(f"📚 Comisión: {comision}")
    if anio:
        partes.append(f"📅 Año: {anio}")
    partes.append(f"📄 Tipo: {tipo}")
    if descripcion:
        partes.append(f"ℹ️ Detalle: {descripcion}")
    if link:
        partes.append(f"🔗 {link}")
    return "\n".join(partes)

def obtener_nombre_materia(bd, id_materia):
    """ devuelve el nombre oficial de la materia usando el índice """
    materia = bd['indice_materias_por_id'].get(str(id_materia))
    return materia['nombre_oficial'] if materia else ''

def resolver_sin_distincion(recursos, bd, id_materia):
    """ devuelve el primer recurso cuando no hay forma de distinguir entre varios """
    r = copy.deepcopy(recursos[0])  # no mutar el original
    r['nombre_materia'] = obtener_nombre_materia(bd, id_materia)
    respuesta = formatear_recurso(r, incluir_materia=True)
    if len(recursos) > 1:
        respuesta += "\n⚠️ Nota: Encontré varios registros idénticos, te paso el primero."
    return respuesta

# centraliza la respuesta de un recurso resuelto para no repetir la misma secuencia
# de copiar el diccionario, agregar el nombre de la materia y marcar la consulta como exitosa.
def responder_recurso(usuario_id, bd, id_materia, recurso):
    recurso_copia = copy.deepcopy(recurso)
    recurso_copia['nombre_materia'] = obtener_nombre_materia(bd, id_materia)
    marcar_consulta_exitosa(usuario_id)
    return formatear_recurso(recurso_copia, incluir_materia=True)

# agrupa recursos por profesor para mostrar la lista de opciones de forma legible.
def obtener_profesores_comisiones(recursos):
    profesores_comisiones = defaultdict(set)
    for recurso in recursos:
        profesor = obtener_nombre_profesor(recurso)
        if not profesor:
            continue
        comision = recurso.get('comision', '')
        if comision:
            profesores_comisiones[profesor].add(comision)
    return profesores_comisiones

def interpretar_fecha(mensaje):
    """
    convierte referencias temporales en el mensaje a una fecha YYYY-MM-DD,
    si no encuentra referencia, devuelve la fecha de hoy
    
    """
    hoy = date.today()
    texto = limpiar_texto(mensaje)

    if "mañana" in texto:
        return (hoy + timedelta(days=1)).strftime("%Y-%m-%d")
    if "pasado mañana" in texto:
        return (hoy + timedelta(days=2)).strftime("%Y-%m-%d")
    if "hoy" in texto:
        return hoy.strftime("%Y-%m-%d")

    dias_semana = {
        "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
        "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6
    }
    for dia, num in dias_semana.items():
        if dia in texto:
            dias_hasta = (num - hoy.weekday()) % 7
            if dias_hasta == 0 and "proximo" not in texto:
                # asumir hoy si es el mismo día y no dice próximo
                pass
            fecha = hoy + timedelta(days=dias_hasta)
            return fecha.strftime("%Y-%m-%d")

    return hoy.strftime("%Y-%m-%d")

PALABRAS_TIPO_RECURSO = {
    "aula", "virtual", "grupo", "whatsapp", "wpp", "wsp", "apunte", "apuntes",
    "programa", "programas", "clase", "cursar", "curso", "recurso", "recursos",
    "link", "enlace", "material", "materia"
}
PALABRAS_BUSQUEDA = PALABRAS_TIPO_RECURSO | {"horario", "horarios", "hora", "horas", "bedelia", "donde"}

def limpiar_mensaje_para_materia(mensaje, stopwords_extra=None):
    """
    elimina palabras que indican tipo de recurso (y stopwords extra si se pasan)
    para no confundir la búsqueda de materia
    
    """
    if stopwords_extra is None:
        stopwords_extra = set()
    stopwords = PALABRAS_TIPO_RECURSO | stopwords_extra
    tokens = [t for t in mensaje.split() if t not in stopwords]
    return " ".join(tokens)




######################################
# FUNCIÓN PRINCIPAL DE PROCESAMIENTO #
######################################

# esta es la función principal del bot. recibe el texto del usuario, interpreta la intención,
# busca la materia y devuelve el mensaje final con la respuesta más útil posible.
def procesar_mensaje(mensaje_usuario, bd, usuario_id):
    global sesiones_usuarios

    try:
        mensaje = limpiar_texto(mensaje_usuario)
        limpiar_sesiones_expiradas()

        # -----Comandos especiales----
        tokens = set(mensaje.split())
        if tokens & {"cancelar", "reset", "salir"}:
            sesiones_usuarios.pop(usuario_id, None)
            return "🔄 Búsqueda cancelada. ¿En qué puedo ayudarte?"

        if tokens & {"menu", "ayuda", "help"}:
            sesiones_usuarios.pop(usuario_id, None)
            return (
                "🤖 Hola! Soy el bot de la facultad.\n"
                "Puedo buscar:\n"
                "📄 Programas\n"
                "💻 Aulas virtuales\n"
                "📱 Grupos de WhatsApp\n"
                "📚 Apuntes\n"
                "📍 Horarios\n\n"
                "📝 Feedback\n"
                "Escribí 'feedback' o 'sugerencia' para dejarnos un comentario o reportar un error.\n\n"

                "v.g: \"alguien tiene el programa de Tributario?\"\n"
                "Si necesitás cancelar una búsqueda, escribí Cancelar."
            )




         #   FEEDBACK / SUGERENCIA / ERROR
        PALABRAS_FEEDBACK = {
            "feedback", "sugerencia", "sugerencias", "sugerencia", "error",
            "fallo", "fallos", "bug", "comentario", "comentarios", "reporte",
            "problema", "problemas"
        }

        sesion_actual = obtener_sesion_activa(usuario_id)
        en_estado_feedback = False
        if sesion_actual and str(sesion_actual.get("estado", "")).startswith("esperando_feedback"):
            en_estado_feedback = True

        if tokens & PALABRAS_FEEDBACK and not en_estado_feedback:
            tipo_feedback = "general"
            if tokens & {"sugerencia", "sugerencias"}:
                tipo_feedback = "sugerencia"
            elif tokens & {"error", "fallo", "fallos", "bug", "problema", "problemas"}:
                tipo_feedback = "error"
            elif tokens & {"comentario", "comentarios"}:
                tipo_feedback = "comentario"

            sesiones_usuarios.pop(usuario_id, None)
            guardar_sesion(usuario_id, {
                "estado": "esperando_feedback_mensaje",
                "tipo_feedback": tipo_feedback
            })
            return "📝 ¡Gracias por querer ayudar! Contame en tu próximo mensaje cuál es la sugerencia, error o comentario que querés reportar."

######-----joke de cortesia-----#####

        if es_agradecimiento_puro(mensaje):
            memoria = memoria_cortesia.get(usuario_id)
            
            memoria_redis = obtener_memoria_cortesia(usuario_id)
            if memoria_redis is not None:
                memoria = memoria_redis

            if not memoria or not memoria.get("ayudado"):
                limpiar_cortesia(usuario_id)
                return "No me preguntaste nada, pero un placer ayudar, supongo 😁"

            memoria["gracias_count"] += 1
            memoria["ultimo_acceso"] = time.time() # renueva el temporizador
            guardar_memoria_cortesia(usuario_id, memoria)
            count = memoria["gracias_count"]
            
            if count == 1:
                return "De nada! 😎"
            elif count == 2:
                return "De nada. 😅"
            elif count == 3:
                return "De nada. 😒"
            else:
                return "🫩"


        
        # ----CONSULTA DE HORARIOS-----
        if (tokens & {"horario", "horarios", "hora", "horas", "clase", "bedelia", "donde", "curso", "cursar"}) or ("aula" in tokens and "virtual" not in tokens):
            # limpia sesión anterior
            sesiones_usuarios.pop(usuario_id, None)

            # usamos el mensaje original (sin filtrar) para detectar sinónimos compuestos
            id_materia = encontrar_materia(mensaje, bd)
            if not id_materia:
                return "❌ ¿De qué materia buscás el horario o aula? Escribime el nombre."

            fecha_consulta = interpretar_fecha(mensaje)
            nombre_mat = obtener_nombre_materia(bd, id_materia)

            horarios_materia = [
                h for h in bd['horarios']
                if str(h.get('id_materia', '')) == str(id_materia)
                and str(h.get('fecha', '')) == fecha_consulta
            ]

            if not horarios_materia:
                return f"📅 No tengo horarios para *{nombre_mat}* el {fecha_consulta}."

            horarios_unicos = []
            for h in horarios_materia:
                if h not in horarios_unicos:
                    horarios_unicos.append(h)

            if fecha_consulta == date.today().strftime("%Y-%m-%d"):
                encabezado = f"📍 *Horarios de hoy para {nombre_mat}:*"
            else:
                encabezado = f"📍 *Horarios del {fecha_consulta} para {nombre_mat}:*"

            respuestas = [encabezado]
            for h in horarios_unicos:
                estado = h.get('estado', '✅ NORMAL')
                horario = h.get('horario', '')
                aula = h.get('aula', 'Sin aula')
                docentes = h.get('docentes', '')
                detalle = h.get('detalle', '')
                texto_detalle = f" | ℹ️ {detalle}" if detalle else ""
                respuestas.append(f"{estado} | {horario} | Aula: {aula} ({docentes}){texto_detalle}")

            marcar_consulta_exitosa(usuario_id)
            return "\n".join(respuestas)




        # ----MANEJO DE ESTADOS------ 
        if usuario_id in sesiones_usuarios:
            sesion = sesiones_usuarios[usuario_id]
            estado = sesion.get("estado")

            if estado == "esperando_materia":
                tipo_buscado = sesion["tipo_recurso"]
                profesor_mencionado = sesion.get("profesor_mencionado", "")

                # limpia tipo de recurso para buscar materia
                mensaje_limpio = limpiar_mensaje_para_materia(mensaje)
                id_materia = encontrar_materia(mensaje_limpio, bd)
                if not id_materia:
                    return "❌ No pude identificar la materia. ¿Podrías escribir el nombre exacto?"

                del sesiones_usuarios[usuario_id]

                posibles_recursos = copy.deepcopy(
                    bd['indice_recursos'].get((str(id_materia), tipo_buscado), [])
                )
                resultados_finales = filtrar_por_profesor(posibles_recursos, profesor_mencionado)

                if not resultados_finales:
                    return "❌ No encontré recursos para esa combinación de materia y profesor."

                if len(resultados_finales) == 1:
                    return responder_recurso(usuario_id, bd, id_materia, resultados_finales[0])
                else:
                    return _manejar_multiples_resultados(
                        usuario_id, resultados_finales, bd, id_materia,
                        tipo_buscado, mensaje
                    )

            elif estado == "esperando_profesor":
                recursos_guardados = sesion["opciones_disponibles"]
                id_materia = sesion["id_materia"]
                tipo_buscado = sesion["tipo_recurso"]

                profesor_detectado = detectar_profesor_en_lista(mensaje, recursos_guardados)
                if not profesor_detectado:
                    return "❌ No reconocí ese profesor. Elegí uno de los mencionados."

                resultados_finales = filtrar_por_profesor(recursos_guardados, profesor_detectado)
                if not resultados_finales:
                    return "❌ Ese profesor no está en la lista."

                if len(resultados_finales) == 1:
                    del sesiones_usuarios[usuario_id]
                    return responder_recurso(usuario_id, bd, id_materia, resultados_finales[0])
                else:
                    sesion["profesor"] = profesor_detectado
                    sesion["opciones_disponibles"] = resultados_finales
                    return _manejar_multiples_resultados(
                        usuario_id, resultados_finales, bd, id_materia,
                        tipo_buscado, mensaje
                    )

            elif estado == "esperando_comision":
                opciones = sesion["opciones_disponibles"]
                id_materia = sesion["id_materia"]

                # intenta detectar si respondió con una cátedra/profesor
                profesor_detectado = detectar_profesor_en_lista(mensaje, opciones)
                if profesor_detectado:
                    opciones_filtradas = filtrar_por_profesor(opciones, profesor_detectado)
                    if not opciones_filtradas:
                        del sesiones_usuarios[usuario_id]
                        return "❌ Ese profesor no está en la lista."

                    # re evalua con la cátedra filtrada
                    return _manejar_multiples_resultados(
                        usuario_id, opciones_filtradas, bd, id_materia,
                        sesion["tipo_recurso"], mensaje 
                    )

                palabras_mensaje = set(mensaje.split())
                comision_encontrada = None
                for r in opciones:
                    comision_limpia = limpiar_texto(r.get('comision', ''))
                    if coinciden_comision(mensaje, comision_limpia):
                        # Buscar la comisión canónica del recurso
                        comision_encontrada = comision_limpia
                        break

                if not comision_encontrada:
                    comisiones_validas = extraer_comisiones(opciones)
                    return f"❌ No reconocí la comisión. Las disponibles son: {', '.join(comisiones_validas)}"

                resultados_finales = [r for r in opciones if limpiar_texto(r.get('comision', '')) == comision_encontrada]
                del sesiones_usuarios[usuario_id]

                if len(resultados_finales) == 1:
                    return responder_recurso(usuario_id, bd, id_materia, resultados_finales[0])
                else:
                    marcar_consulta_exitosa(usuario_id)
                    return resolver_sin_distincion(resultados_finales, bd, id_materia)

            elif estado == "esperando_anio":
                opciones = sesion["opciones_disponibles"]
                id_materia = sesion["id_materia"]

                anio_encontrado = None
                for r in opciones:
                    anio_limpio = limpiar_texto(r.get('anio', ''))
                    if anio_limpio and anio_limpio in mensaje:
                        anio_encontrado = anio_limpio
                        break

                if not anio_encontrado:
                    anios_validos = extraer_anios(opciones)
                    return f"❌ No reconocí el año. Los disponibles son: {', '.join(anios_validos)}"

                resultados_finales = [r for r in opciones if limpiar_texto(r.get('anio', '')) == anio_encontrado]
                del sesiones_usuarios[usuario_id]
                marcar_consulta_exitosa(usuario_id)
                return resolver_sin_distincion(resultados_finales, bd, id_materia)

            # ---NUEVOS ESTADOS DE FEEDBACK-----
            elif estado == "esperando_feedback_mensaje":
                # el usuario acaba de escribir el mensaje de feedback
                tipo_feedback = sesion.get("tipo_feedback", "general")
                mensaje_feedback = mensaje.strip()

                sesion["estado"] = "esperando_feedback_contacto"
                sesion["mensaje_feedback"] = mensaje_feedback
                guardar_sesion(usuario_id, sesion)

                return "✅ Mensaje recibido. ¿Querés dejar tu nombre o algún contacto para que podamos responderte? (responde 'si' o 'no')"

            elif estado == "esperando_feedback_contacto":
                texto = mensaje.lower()
                if texto in {"si", "sí", "dale", "ok", "yes"}:
                    sesion["estado"] = "esperando_feedback_contacto_datos"
                    guardar_sesion(usuario_id, sesion)
                    return "👍 Escribí tu nombre y contacto (ej: Dalmacio, @velezdalma o 342-546-5678)"
                else:
                    from feedback import guardar_feedback
                    tipo_feedback = sesion.get("tipo_feedback", "general")
                    mensaje_feedback = sesion.get("mensaje_feedback", "")
                    guardado = guardar_feedback(usuario_id, tipo_feedback, mensaje_feedback, "")
                    del sesiones_usuarios[usuario_id]
                    if guardado:
                        marcar_consulta_exitosa(usuario_id)
                        return "🎉 ¡Gracias por tu ayuda! Tu mensaje fue guardado."
                    return "⚠️ No pude registrar tu mensaje en este momento. Intentá de nuevo más tarde."

            elif estado == "esperando_feedback_contacto_datos":
                from feedback import guardar_feedback
                tipo_feedback = sesion.get("tipo_feedback", "general")
                mensaje_feedback = sesion.get("mensaje_feedback", "")
                contacto = mensaje.strip()
                guardado = guardar_feedback(usuario_id, tipo_feedback, mensaje_feedback, contacto)
                del sesiones_usuarios[usuario_id]
                if guardado:
                    marcar_consulta_exitosa(usuario_id)
                    return "🎉 ¡Gracias por tu ayuda! Tu mensaje y contacto fueron guardados."
                return "⚠️ No pude registrar tu mensaje y contacto en este momento. Intentá de nuevo más tarde."



        # ----SIN ESTADO PREVIO: PROCESAMIENTO NORMAL---
        tipo_buscado = detectar_tipo_recurso(mensaje)
        if tipo_buscado is None:
            return "❓ ¿Qué tipo de recurso buscás? Puede ser programa, apunte, aula virtual o grupo de WhatsApp."

        # limpia palabras de tipo de recurso antes de buscar materia
        mensaje_limpio = limpiar_mensaje_para_materia(mensaje)
        id_materia = encontrar_materia(mensaje_limpio, bd)

        if not id_materia:
            # busca por profesor
            profesor_detectado, materias_asociadas, _ = detectar_profesor_en_recursos(mensaje, bd)
            if profesor_detectado and materias_asociadas:
                if len(materias_asociadas) == 1:
                    id_materia = materias_asociadas[0]
                    posibles_recursos = copy.deepcopy(
                        bd['indice_recursos'].get((str(id_materia), tipo_buscado), [])
                    )
                    posibles_recursos = filtrar_por_profesor(posibles_recursos, profesor_detectado)

                    if not posibles_recursos:
                        return f"❌ No tengo ese recurso para {profesor_detectado}."

                    if len(posibles_recursos) == 1:
                        return responder_recurso(usuario_id, bd, id_materia, posibles_recursos[0])
                    else:
                        return _manejar_multiples_resultados(
                            usuario_id, posibles_recursos, bd, id_materia,
                            tipo_buscado, mensaje
                        )
                else:
                    # profesor con varias materias
                    guardar_sesion(usuario_id, {
                        "estado": "esperando_materia",
                        "tipo_recurso": tipo_buscado,
                        "profesor_mencionado": profesor_detectado
                    })
                    nombres_materias = []
                    for id_mat in materias_asociadas:
                        nombre = obtener_nombre_materia(bd, id_mat)
                        if nombre:
                            nombres_materias.append(nombre)
                    return f"🔍 {profesor_detectado} está en varias materias: {', '.join(nombres_materias)}.\n¿De cuál materia necesitás el recurso?"
            else:
                # si hay tipo de recurso, guardar sesión para pedir la materia
                if tipo_buscado:
                    guardar_sesion(usuario_id, {
                        "estado": "esperando_materia",
                        "tipo_recurso": tipo_buscado,
                        "profesor_mencionado": ""
                    })
                    nombres_tipo = {
                        "aula_virtual": "aula virtual",
                        "grupo_wsp": "grupo de WhatsApp",
                        "apuntes": "apunte",
                        "programa": "programa"
                    }
                    recurso_legible = nombres_tipo.get(tipo_buscado, tipo_buscado)
                    return f"📚 ¿De qué materia buscás el {recurso_legible}?"
                else:
                    return "❌ No pude identificar la materia ni el profesor. ¿Podrías escribir el nombre de la materia?"

        # materia identificada
        posibles_recursos = copy.deepcopy(
            bd['indice_recursos'].get((str(id_materia), tipo_buscado), [])
        )

        if not posibles_recursos:
            return "❌ No tengo guardado ese recurso para esta materia."

        # intenta filtrar por profesor si se menciona dentro de los filtrados
        profesor_detectado = detectar_profesor_en_lista(mensaje, posibles_recursos)
        if profesor_detectado:
            posibles_recursos = filtrar_por_profesor(posibles_recursos, profesor_detectado)
            if not posibles_recursos:
                return f"❌ No encontré recursos para {profesor_detectado} en esta materia."

        if len(posibles_recursos) == 1:
            return responder_recurso(usuario_id, bd, id_materia, posibles_recursos[0])
        else:
            profesores_unicos = set()
            for r in posibles_recursos:
                profesores_unicos.add(limpiar_texto(obtener_nombre_profesor(r)))

            if len(profesores_unicos) == 1:
                return _manejar_multiples_resultados(
                    usuario_id, posibles_recursos, bd, id_materia,
                    tipo_buscado, mensaje
                )
            else:
                # varios profesores
                guardar_sesion(usuario_id, {
                    "estado": "esperando_profesor",
                    "id_materia": id_materia,
                    "tipo_recurso": tipo_buscado,
                    "opciones_disponibles": posibles_recursos
                })

                profesores_comisiones = obtener_profesores_comisiones(posibles_recursos)

                respuesta = "📚 Encontré varias opciones. ¿De cuál cátedra o profe buscás?\n\n"
                for prof, comisiones in profesores_comisiones.items():
                    if comisiones:
                        respuesta += f"👉 {prof} (Comisiones: {', '.join(sorted(comisiones))})\n"
                    else:
                        respuesta += f"👉 {prof}\n"
                return respuesta

    except Exception as error:
        logger.exception("Error procesando mensaje para usuario %s: %s", usuario_id, mensaje_usuario)
        sesiones_usuarios.pop(usuario_id, None)
        return "⚠️ Ups, ocurrió un error. Intentá de nuevo."

# esta función resuelve los casos en los que el usuario tiene varias opciones posibles.
# pregunta por profesor, comisión o año para reducir la lista hasta llegar a un único resultado.
def _manejar_multiples_resultados(usuario_id, recursos, bd, id_materia, tipo_buscado, mensaje=None):
    """ maneja la lógica de selección de profesor, comisión o año """
    # si se pasa el mensaje, intenta detectar comisión/año y filtrar
    if mensaje:
        recursos = filtrar_por_comision_o_anio(mensaje, recursos)
        if len(recursos) == 1:
            return responder_recurso(usuario_id, bd, id_materia, recursos[0])

    # obtiene profesores/cátedras únicos no vacíos
    profesores_unicos = set()
    for r in recursos:
        prof = limpiar_texto(obtener_nombre_profesor(r))
        if prof:
            profesores_unicos.add(prof)

    nombre_materia = obtener_nombre_materia(bd, id_materia)

    # 1 si hay más de un profesor/cátedra, preguntar por cátedra
    if len(profesores_unicos) > 1:
        guardar_sesion(usuario_id, {
            "estado": "esperando_profesor",
            "id_materia": id_materia,
            "tipo_recurso": tipo_buscado,
            "opciones_disponibles": recursos
        })

        # agrupa comisiones por profesor para mostrar info útil
        profesores_comisiones = obtener_profesores_comisiones(recursos)

        respuesta = f"📚 Encontré varias opciones para *{nombre_materia}*. ¿De cuál cátedra o profe buscás?\n\n"
        for prof, comisiones in profesores_comisiones.items():
            if comisiones:
                respuesta += f"👉 {prof} (Comisiones: {', '.join(sorted(comisiones))})\n"
            else:
                respuesta += f"👉 {prof}\n"
        return respuesta

    # 2 si hay un solo profesor (o ninguno), preguntar por comisión o año
    comisiones_unicas = extraer_comisiones(recursos)
    anios_unicos = extraer_anios(recursos)

    if len(comisiones_unicas) > 1:
        profesor_legible = list(profesores_unicos)[0] if profesores_unicos else nombre_materia
        guardar_sesion(usuario_id, {
            "estado": "esperando_comision",
            "id_materia": id_materia,
            "tipo_recurso": tipo_buscado,
            "profesor": profesor_legible,
            "opciones_disponibles": recursos
        })
        lista = ", ".join(comisiones_unicas)
        return f"📚 Tenés varias comisiones para *{profesor_legible}*: {lista}.\n¿Cuál comisión buscás?"

    if len(anios_unicos) > 1:
        profesor_legible = list(profesores_unicos)[0] if profesores_unicos else nombre_materia
        guardar_sesion(usuario_id, {
            "estado": "esperando_anio",
            "id_materia": id_materia,
            "tipo_recurso": tipo_buscado,
            "profesor": profesor_legible,
            "opciones_disponibles": recursos
        })
        lista = ", ".join(anios_unicos)
        return f"📅 Hay varias versiones para *{profesor_legible}*. ¿De qué año buscás? ({lista})"

    # 3) Sin distinción
    marcar_consulta_exitosa(usuario_id)
    return resolver_sin_distincion(recursos, bd, id_materia)





#####################
# BLOQUE DE PRUEBAS #
#####################
if __name__ == "__main__":
    from conexion import hermes_bd

    base_datos = hermes_bd()
    if base_datos:
        print("\n--- SIMULADOR ---")
        print("Escribe un mensaje de prueba. 'salir' para terminar.\n")
        while True:
            mensaje_prueba = input("👤 Alumno: ")
            if mensaje_prueba.lower() == 'salir':
                break
            respuesta = procesar_mensaje(mensaje_prueba, base_datos, usuario_id="alumno_1")
            print(f"🤖 Bot: {respuesta}\n")
    else:
        print("❌ No se pudo cargar la base de datos.")