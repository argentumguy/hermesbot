# Verificación y códigos de invitación del bot Hermes

Este documento describe la nueva capa de acceso del bot para permitir que solo usuarios verificados puedan interactuar con él.

## Objetivo

El bot queda protegido por una validación previa de acceso:
- si el número no está verificado, el bot no responde
- si el número está verificado, puede consultar recursos normalmente
- un usuario verificado puede generar códigos para invitar a otras personas
- una persona que recibe un código puede auto-verificarse usando ese código
- los códigos tienen vencimiento y no se pueden reutilizar

---

## Comandos disponibles

### Generar código
- generar codigo
- generar invitacion
- codigo
- invitar

### Usar un código
- HERMES-AB12CD

### Ayuda
- ayuda
- menu
- help

### Cancelar
- cancelar
- reset
- salir

---

## Estructura de Google Sheets

### Usuarios
- numero
- estado
- invitado_por
- codigo_usado
- fecha_alta
- cantidad_invitados
- ultimo_mensaje
- fecha_ultimo_acceso

### CodigosInvitacion
- codigo
- creado_por
- estado
- fecha_creacion
- fecha_expiracion
- usado_por
- fecha_uso
- comentario

### Configuracion
- nombre_parametro
- valor
- descripcion

Valores base:
- modo_validacion = whitelist
- duracion_codigo_horas = 168
- maximo_codigos_por_usuario = 3
- respuesta_usuarios_no_verificados = silencio
- permitir_autoverificacion = true
- redis_activado = false

---

## Reglas

- un usuario no verificado no recibe respuesta
- un usuario verificado puede generar un código
- un código solo sirve una vez
- un código vencido no sirve
- un usuario con código válido queda verificado automáticamente

---

## Archivos involucrados

- app.py
- control_acceso.py
- motor.py
- conexion.py
