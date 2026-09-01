# Manual de verificación del bot Hermes

## Objetivo

Este bot queda protegido por una capa de acceso para que solo puedan interactuar con él los usuarios verificados.

## Flujo

### Usuario verificado genera código
1. Escribe: generar codigo
2. El bot devuelve un código
3. El código queda guardado en Sheets

### Usuario nuevo usa código
1. Escribe el código recibido
2. El sistema lo valida
3. El número queda verificado

### Usuario sin acceso
1. Envía un mensaje
2. El bot lo ignora
3. No responde

## Configuración recomendada

### Hoja Configuracion
- modo_validacion = whitelist
- duracion_codigo_horas = 168
- maximo_codigos_por_usuario = 3
- respuesta_usuarios_no_verificados = silencio
- permitir_autoverificacion = true
- redis_activado = false

## Recomendación

Mantener la validación en Sheets y dejar Redis apagado por ahora.
