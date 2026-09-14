# Contrato de datos: n8n -> Dashboard

Detalle campo por campo de los tres archivos que el workflow de n8n `Dashboard GitHub`
(`tZy2OwAQAXNo44m8`) debe publicar en la raíz de este repo en cada ejecución. Es la
versión formal usada por `scripts/validate_dashboard_json.py`; la versión narrativa
vive en `N8N_DASHBOARD_EXPORT_SPEC.md`.

## SYSTEM.json

Objeto raíz. Campos requeridos:

- `generated_at` (string, ISO datetime) — timestamp del run que generó el archivo.
- `last_execution` (string, `HH:mm`)
- `executions_today` (number)
- `successful_today` (number)
- `active_workflows` (number)
- `critical_alerts` (number)
- `success_rate` (number)
- `health` (string) — uno de `healthy`, `degraded`, `critical`
- `recent_executions` (array de objetos) — cada uno con al menos `id`, `workflow_id`,
  `workflow_name`, `status`, `mode`, `started_at`
- `workflows` (array de objetos) — cada uno con al menos `id`, `name`, `active`,
  `executions_today`, `errors_today`, `success_rate`, `last_status`
- `hourly_activity` (array de objetos) — cada uno con `hour` (`HH:00`), `total`, `errors`

## OPERATIONS_HISTORY.json

Objeto raíz con una sola clave:

- `snapshots` (array, requerido, no vacío)
  - cada snapshot: `date` (string `YYYY-MM-DD`), `executions_today` (number),
    `success_rate` (number), `critical_alerts` (number)
  - orden: más reciente primero (`snapshots[0].date >= snapshots[1].date >= ...`)
  - un snapshot por `date` (sin duplicados)
  - se recomienda mantener al menos 7 snapshots cuando el flow lo permita (advertencia,
    no error duro)

## DASHBOARD_CONTEXT.json

Objeto raíz. Campos requeridos, todos arrays:

- `projects`: cada item con `name` (string), `progress` (string, ej. `"65%"`), `status`
  (string, ej. `Active` / `Paused` / `Done`)
- `tasks`: cada item con `text` (string), `done` (boolean)
- `notes`: array de strings
- `changes`: cada item con `title` (string), `meta` (string), `impact` (string)

## Señales de una publicación sana

- `SYSTEM.json.generated_at` corresponde al run actual (no quedó pegado de una corrida
  anterior).
- `OPERATIONS_HISTORY.json` cambia cuando cambia el día o los números del día.
- `DASHBOARD_CONTEXT.json` no trae texto roto (mojibake / JSON escapado a medias).
- El dashboard no cae en el fallback de `PROJECTS.md` / `TASKS.md` / `NOTES.md` cuando
  el flow corrió bien — si cae en fallback con los tres archivos presentes, es señal de
  JSON inválido o desactualizado, no de que falten los archivos.
