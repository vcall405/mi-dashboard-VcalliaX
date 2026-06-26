# N8N Dashboard Export Spec

Workflow objetivo: `tZy2OwAQAXNo44m8`

Este dashboard espera que el flow publique tres archivos en GitHub Pages:

1. `SYSTEM.json`
2. `OPERATIONS_HISTORY.json`
3. `DASHBOARD_CONTEXT.json`

## 1. SYSTEM.json

Fuente operativa principal. Debe incluir:

- `generated_at`: ISO datetime
- `last_execution`: string `HH:mm`
- `executions_today`: number
- `successful_today`: number
- `active_workflows`: number
- `critical_alerts`: number
- `success_rate`: number
- `health`: `healthy | degraded | critical`
- `recent_executions`: array
- `workflows`: array
- `hourly_activity`: array

## 2. OPERATIONS_HISTORY.json

Fuente para comparativas simples.

Formato:

```json
{
  "snapshots": [
    {
      "date": "2026-06-23",
      "executions_today": 32,
      "success_rate": 97,
      "critical_alerts": 1
    }
  ]
}
```

Reglas:

- ordenar del mas reciente al mas antiguo
- mantener al menos 7 snapshots si el flow puede
- un snapshot por dia

## 3. DASHBOARD_CONTEXT.json

Contexto humano estructurado. Debe incluir:

- `projects`: `{ name, progress, status }[]`
- `tasks`: `{ text, done }[]`
- `notes`: `string[]`
- `changes`: `{ title, meta, impact }[]`

## Reglas de publicacion

- publicar siempre los tres archivos en la misma ejecucion
- si falla uno, marcar error y no dejar mezcla de versiones
- usar texto limpio ASCII cuando no sea necesario unicode
- preferir datos estructurados sobre markdown
- dejar `PROJECTS.md`, `TASKS.md` y `NOTES.md` solo como fallback

## Orden recomendado dentro del flow

1. recolectar estado n8n
2. construir `SYSTEM.json`
3. leer snapshot previo de `OPERATIONS_HISTORY.json`
4. insertar o actualizar snapshot del dia
5. construir `DASHBOARD_CONTEXT.json`
6. publicar archivos a GitHub
7. registrar resultado del publish

## Senales de validacion

- `SYSTEM.json` trae `generated_at` del mismo run
- `OPERATIONS_HISTORY.json` cambia cuando cambia el dia o los datos del dia
- `DASHBOARD_CONTEXT.json` no contiene texto roto
- el dashboard no cae en fallback cuando el flow corre bien
