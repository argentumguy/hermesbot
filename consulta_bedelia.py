#consulta_bedelia.py

import os
import requests
import gspread
from bs4 import BeautifulSoup
from datetime import date, timedelta
from dotenv import load_dotenv

from conexion import hermes_bd
from utils import limpiar_texto

load_dotenv()

# estas variables de entorno apuntan a la credencial de Google y al documento
# central donde se guarda la información de horarios del sistema.
CREDENCIALES = os.getenv("GOOGLE_CREDENTIALS_PATH", "credenciales.json")
ID_DOCUMENTO = os.getenv("GOOGLE_SHEETS_ID")

# ---------- FUNCIONES ----------

# esta función intenta identificar una materia a partir del nombre que llega
# desde Bedelía, usando una comparación más estricta que el motor de lenguaje natural.
def asignar_id_estricto(nombre_bedelia, bd):
    """
    busca el ID mediante coincidencia exacta de subcadenas,
    evitando los falsos positivos del motor NLP
    
    """
    nombre_limpio = limpiar_texto(nombre_bedelia).replace("cursado", "").strip()

    mejor_id = ""
    max_longitud = 0

    for mat in bd['materias']:
        nombre_db = limpiar_texto(str(mat.get('nombre_normalizado', '')))

        if len(nombre_db) > 3 and nombre_db in nombre_limpio:
            if len(nombre_db) > max_longitud:
                max_longitud = len(nombre_db)
                mejor_id = mat['id_materia']

    return str(mejor_id) if mejor_id else ""


def rango_hasta_sabado():
    """
    devuelve (fecha_inicio, fecha_fin) donde inicio es hoy y fin es el sábado
    de la semana actual (o próximo sábado si hoy es sábado o domingo)
    
    """
    hoy = date.today()
    dias_hasta_sabado = (5 - hoy.weekday()) % 7
    if dias_hasta_sabado == 0:
        dias_hasta_sabado = 7  # si hoy es sábado, tomar el próximo sábado
    fin = hoy + timedelta(days=dias_hasta_sabado)
    return hoy, fin


def obtener_horarios_fcjs(fecha_buscada=None):
    """
    se conecta a la web de Bedelía, extrae la tabla de horarios
    y devuelve una lista de diccionarios con los datos crudos.
    Si fecha_buscada es None, consulta los horarios de hoy.
    Si se especifica fecha_buscada (YYYY-MM-DD), consulta esa fecha.
    
    """
    url = "https://servicios.unl.edu.ar/bedeliamovil/jYruY"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        if fecha_buscada:
            print(f"Consultando horarios para la fecha: {fecha_buscada}...")
            payload = {'fecha': fecha_buscada}
            respuesta = requests.post(url, headers=headers, data=payload, timeout=20)
        else:
            print("Consultando horarios de HOY...")
            respuesta = requests.get(url, headers=headers, timeout=20)

        if respuesta.status_code != 200:
            print(f"Error al acceder a la página: {respuesta.status_code}")
            return []

        soup = BeautifulSoup(respuesta.text, 'html.parser')
        tbody = soup.find('tbody')

        if not tbody:
            print("No se encontró la tabla de horarios.")
            return []

        filas = tbody.find_all('tr')
        datos_extraidos = []

        fecha_registro = fecha_buscada if fecha_buscada else date.today().strftime("%Y-%m-%d")

        for fila in filas:
            columnas = fila.find_all('td')

            if len(columnas) >= 3:
                horario = columnas[0].text.strip()
                clases_horario = columnas[0].get('class', [])
                estado = "⚠️ SUSPENDIDA" if 'tachado-suspendida' in clases_horario else "✅ NORMAL"

                texto_materia = columnas[1].text.strip()
                palabras_molestas = ["SUSPENDIDA", "* PARO DOCENTE", "DESCONOCIDO", "\n", "\r"]
                for palabra in palabras_molestas:
                    texto_materia = texto_materia.replace(palabra, " ")

                partes_materia = texto_materia.split(' - ')

                if len(partes_materia) >= 3:
                    materia = partes_materia[0].strip()
                    docentes = partes_materia[-1].strip()
                    detalle = " - ".join([p.strip() for p in partes_materia[1:-1]])
                elif len(partes_materia) == 2:
                    materia = partes_materia[0].strip()
                    docentes = partes_materia[1].strip()
                    detalle = ""
                else:
                    materia = texto_materia.strip()
                    docentes = "-"
                    detalle = ""

                texto_aula = columnas[2].text.strip()
                partes_aula = texto_aula.split(' - ')
                aula = partes_aula[0].strip() if len(partes_aula) > 0 else texto_aula

                registro = {
                    'fecha': fecha_registro,
                    'estado': estado,
                    'horario': horario,
                    'materia': materia,
                    'docentes': docentes,
                    'aula': aula,
                    'detalle': detalle
                }
                datos_extraidos.append(registro)

        return datos_extraidos

    except requests.exceptions.RequestException as e:
        print(f"❌ Error de conexión con Bedelía: {e}")
        return []
    except Exception as e:
        print(f"❌ Error inesperado al obtener horarios: {e}")
        return []


def actualizar_hoja_horarios_rango(datos_procesados, fecha_inicio, fecha_fin):
    """
    reemplaza en la hoja 'Horarios' únicamente los registros cuya fecha
    esté dentro del rango [fecha_inicio, fecha_fin],
    los registros fuera del rango se conservan intactos
    
    """
    if not datos_procesados:
        print("❌ No hay datos para actualizar. Se conserva la hoja anterior.")
        return False

    print(f"Conectando a Google Sheets para actualizar horarios del {fecha_inicio} al {fecha_fin}...")

    try:
        cliente = gspread.service_account(filename=CREDENCIALES)
        documento = cliente.open_by_key(ID_DOCUMENTO)
        hoja = documento.worksheet("Horarios")

        # lee todos los valores actuales
        valores_actuales = hoja.get_all_values()

        # definir encabezado esperado
        encabezado = ["id_materia", "fecha", "estado", "horario", "materia", "docentes", "aula", "detalle"]

        filas_conservadas = [encabezado]

        if len(valores_actuales) > 0:
            encabezado_actual = valores_actuales[0]
            try:
                idx_fecha = encabezado_actual.index("fecha")
            except ValueError:
                idx_fecha = None

            for fila in valores_actuales[1:]:
                # si no hay columna fecha o la fila no tiene esa columna, conservar
                if idx_fecha is None or idx_fecha >= len(fila):
                    filas_conservadas.append(fila)
                    continue

                fecha_str = fila[idx_fecha].strip()
                try:
                    fecha_fila = date.fromisoformat(fecha_str)
                except ValueError:
                    # fecha mal formateada, conserva para no perder datos
                    filas_conservadas.append(fila)
                    continue

                # si está dentro del rango a reemplazar, se omite
                if fecha_inicio <= fecha_fila <= fecha_fin:
                    continue
                else:
                    filas_conservadas.append(fila)

        # prepara nuevas filas con los datos scrapeados
        filas_nuevas = []
        for dato in datos_procesados:
            fila = [
                dato.get('id_materia', ''),
                dato.get('fecha', ''),
                dato.get('estado', ''),
                dato.get('horario', ''),
                dato.get('materia', ''),
                dato.get('docentes', ''),
                dato.get('aula', ''),
                dato.get('detalle', '')
            ]
            filas_nuevas.append(fila)

        # combina y escribe en una sola operación
        todas_las_filas = filas_conservadas + filas_nuevas

        # limpia y escribe
        hoja.clear()
        hoja.update('A1', todas_las_filas, value_input_option="USER_ENTERED")

        print(f"✅ Pestaña 'Horarios' actualizada. Se reemplazaron los registros del {fecha_inicio} al {fecha_fin}.")
        print(f"   {len(filas_nuevas)} registros nuevos guardados.")
        return True

    except Exception as e:
        print(f"❌ Error al actualizar la hoja de horarios: {e}")
        return False


# este es el punto de entrada del scraping de horarios.
# coordina la consulta a Bedelía, el cruza de materias y la actualización de la hoja.
def orquestador_scraper():
    """
    función principal:
    
    1. determina el rango desde hoy hasta el sábado
    2. scrapea todos los días del rango
    3. cruza IDs de materias
    4. actualiza la hoja reemplazando solo ese rango
    
    """
    print("--- INICIANDO ACTUALIZACIÓN DE HORARIOS ---")

    hoy, fin = rango_hasta_sabado()
    print(f"Rango de fechas a scrapear: {hoy} → {fin}")

    # carga base de datos
    print("Cargando memoria principal para cruce de datos...")
    bd = hermes_bd()
    if not bd:
        print("❌ Se abortó la actualización porque no se pudo cargar la base de datos local.")
        return

    # consulta cada día del rango
    datos_totales = []
    fecha_actual = hoy
    while fecha_actual <= fin:
        datos = obtener_horarios_fcjs(fecha_actual.strftime("%Y-%m-%d"))
        if datos:
            datos_totales.extend(datos)
            print(f"Se obtuvieron {len(datos)} registros para {fecha_actual}")
        else:
            print(f"No se obtuvieron datos para {fecha_actual}")
        fecha_actual += timedelta(days=1)

    if not datos_totales:
        print("❌ No hay datos de horarios para el rango.")
        return

    # cruza con iDs
    print(f"Procesando {len(datos_totales)} registros...")
    for dato in datos_totales:
        id_encontrado = asignar_id_estricto(dato['materia'], bd)
        dato['id_materia'] = id_encontrado if id_encontrado else ""

    # actualiza la hoja
    actualizar_hoja_horarios_rango(datos_totales, hoy, fin)
    print("--- ACTUALIZACIÓN FINALIZADA ---")


if __name__ == "__main__":
    orquestador_scraper()