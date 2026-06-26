# Flow Gap Analysis

Workflow revisado: `tZy2OwAQAXNo44m8`
Nombre actual: `Dashboard GitHub`

## Lo que hace hoy

El workflow actual:

1. corre cada 30 minutos
2. consulta ejecuciones n8n
3. consulta workflows activos
4. obtiene el SHA actual de `SYSTEM.json`
5. construye metricas en `Preparar Datos`
6. hace `PUT` a GitHub solo para `SYSTEM.json`
7. manda alerta Telegram si detecta errores recientes

## Nodos actuales relevantes

- `Cada 30 min`
- `Consultar Ejecuciones`
- `Consultar Workflows`
- `Obtener SHA del archivo`
- `Preparar Datos`
- `Actualizar GitHub`
- `Hay Errores Recientes`
- `Alerta Telegram`

## Hallazgo principal

Hoy el flow publica un solo archivo:

- `SYSTEM.json`

No publica:

- `OPERATIONS_HISTORY.json`
- `DASHBOARD_CONTEXT.json`

## Impacto en el dashboard

Con la automatizacion actual:

- el panel principal si recibe la fuente operativa base
- las comparativas historicas dependen de un archivo separado que hoy no genera n8n
- el contexto estructurado depende de un archivo separado que hoy no genera n8n
- por eso el dashboard queda parcialmente automatico y parcialmente manual

## Ajustes que faltan en n8n

### 1. Publicar `OPERATIONS_HISTORY.json`

Hace falta:

- leer el archivo actual desde GitHub
- decodificar su JSON
- insertar o actualizar el snapshot del dia
- volver a publicarlo con su nuevo SHA

### 2. Publicar `DASHBOARD_CONTEXT.json`

Hace falta:

- decidir la fuente de proyectos, tareas, notas y cambios
- construir el JSON estructurado
- leer SHA actual del archivo
- hacer `PUT` del nuevo contenido a GitHub

### 3. Evitar versiones mezcladas

Idealmente el flow debe:

- construir los 3 payloads primero
- publicar `SYSTEM.json`
- publicar `OPERATIONS_HISTORY.json`
- publicar `DASHBOARD_CONTEXT.json`
- si uno falla, dejar registro claro del fallo

## Salida esperada final

El workflow debe terminar publicando en el mismo run:

1. `SYSTEM.json`
2. `OPERATIONS_HISTORY.json`
3. `DASHBOARD_CONTEXT.json`

## Estado actual

- `SYSTEM.json`: automatizado
- `OPERATIONS_HISTORY.json`: pendiente
- `DASHBOARD_CONTEXT.json`: pendiente
- alertas Telegram: automatizado
