# utils.py

import re
import unicodedata

# palabras vacías que no aportan a la coincidencia.
# se usan para reducir falsos positivos al comparar nombres de materias,
# profesores y recursos con mensajes naturales del usuario.
STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "y", "o", "u", "en", "a", "con", "por", "para", "que", "del",
    "al", "como", "su", "sus", "es", "son", "mi", "tu", "el", "ella",
    "nos", "les", "lo", "le", "se", "me", "te", "os", "entre", "hacia",
    "sin", "sobre", "tras", "desde", "hasta", "según", "durante"
}

# esta función normaliza cualquier texto para comparar nombres sin quedar
# atado a mayúsculas, tildes, signos de puntuación o espacios extra.
def limpiar_texto(texto):
    """
    normaliza a minúsculas, elimina tildes y reemplaza puntuación
    por espacios para evitar tokens pegados
    
    """
    texto = str(texto).lower().strip()
    # descompone caracteres con tilde
    texto = unicodedata.normalize('NFD', texto)
    # elimina marcas diacríticas
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    # reemplaza cualquier carácter que no sea letra, número, ñ o espacio
    texto = re.sub(r'[^a-z0-9ñ\s]', ' ', texto)
    # colapsa espacios múltiples
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto

# esta lógica compara dos mensajes o textos y devuelve verdadero si comparten
# alguna palabra útil, ignorando artículos y conectores que no aportan significado.
def coinciden_palabras(texto1, texto2, longitud_minima=3):
    """
    devuelve True si al menos una palabra de texto1 aparece como palabra
    completa en texto2 (o viceversa), ignorando stopwords
    
    """
    palabras1 = set(limpiar_texto(texto1).split())
    palabras2 = set(limpiar_texto(texto2).split())

    palabras1 = {p for p in palabras1 if len(p) >= longitud_minima and p not in STOPWORDS}
    palabras2 = {p for p in palabras2 if len(p) >= longitud_minima and p not in STOPWORDS}

    return bool(palabras1 & palabras2)

def obtener_nombre_profesor(recurso):
    """
    devuelve el nombre del profesor o, si está vacío, la cátedra
    si ambos están vacíos, devuelve cadena vacía para no ensuciar índices
    
    """
    prof = str(recurso.get('profesor', '')).strip()
    if prof:
        return prof
    return str(recurso.get('catedra', '')).strip()

# esta función reconoce la intención del usuario según palabras clave.
# por ejemplo, si dice "programa", "apunte" o "aula virtual", se deduce
# qué tipo de recurso está buscando para poder filtrar la búsqueda.
def detectar_tipo_recurso(mensaje):
    """
    detecta el tipo de recurso pedido usando tokens exactos
    devuelve None si no detecta un tipo claro
    
    """
    texto = limpiar_texto(mensaje)
    tokens = set(texto.split())

    if "aula" in tokens or "aulas" in tokens or "virtual" in tokens:
        return "aula_virtual"
    if "grupo" in tokens or "whatsapp" in tokens or "wpp" in tokens or "wsp" in tokens:
        return "grupo_wsp"
    if "apunte" in tokens or "apuntes" in tokens or "resumen" in tokens:
        return "apuntes"
    if "programa" in tokens or "programas" in tokens:
        return "programa"

    return None

# esta función prepara la base de datos para responder rápido.
# crea accesos directos por materia, recurso y sinónimo para evitar recorrer
# toda la hoja cada vez que llega un mensaje.
def construir_indices(bd):
    """
    agrega índices de búsqueda rápida a la base de datos
    
    """

    def es_activo(valor):
        v = str(valor).strip().lower()
        return v in {
            "true", "1", "si", "sí", "activo", "verdadero", "yes"
        }

    def normalizar_tipo(tipo):
        t = limpiar_texto(str(tipo)).strip()
        if not t:
            return t
        # normalizar espacios y guiones a guiones bajos
        return re.sub(r'[\s\-]+', '_', t)

    # indice de recursos por (id_materia, tipo_recurso)
    indice_recursos = {}
    for recurso in bd['recursos']:
        id_mat = str(recurso.get('id_materia', '')).strip()
        tipo = normalizar_tipo(recurso.get('tipo_recurso', ''))

        if not es_activo(recurso.get('activo', '')):
            continue

        clave = (id_mat, tipo)
        indice_recursos.setdefault(clave, []).append(recurso)
    bd['indice_recursos'] = indice_recursos

    # indice de sinónimos exactos (alias normalizado -> sinonimo)
    indice_sinonimos = {}
    for sinonimo in bd['sinonimos']:
        alias = limpiar_texto(sinonimo.get('alias', ''))
        if alias:
            # guarda el más largo en caso de duplicados
            if (
                alias not in indice_sinonimos
                or len(alias) > len(limpiar_texto(indice_sinonimos[alias].get('alias', '')))
            ):
                indice_sinonimos[alias] = sinonimo
    bd['indice_sinonimos'] = indice_sinonimos

    # indice profesor/cátedra -> set de id_materia (normalizados)
    indice_profesor_materias = {}
    for recurso in bd['recursos']:
        if not es_activo(recurso.get('activo', '')):
            continue

        nombre_prof = obtener_nombre_profesor(recurso)
        catedra = str(recurso.get('catedra', '')).strip()

        for nombre in {nombre_prof, catedra}:
            if nombre:
                clave = limpiar_texto(nombre)
                indice_profesor_materias.setdefault(clave, set()).add(
                    str(recurso.get('id_materia', '')).strip()
                )
    bd['indice_profesor_materias'] = indice_profesor_materias

    # indice materia por id (string -> diccionario de materia)
    bd['indice_materias_por_id'] = {
        str(m.get('id_materia', '')).strip(): m
        for m in bd['materias']
    }