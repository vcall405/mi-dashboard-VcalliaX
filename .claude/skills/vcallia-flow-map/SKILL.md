---
name: vcallia-flow-map
description: Mapea, audita y valida el pipeline n8n -> GitHub -> Dashboard de este repo (Vcallia Operations Dashboard). Usar cuando se trabaje sobre el workflow de n8n "Dashboard GitHub" (tZy2OwAQAXNo44m8), cuando se audite qué archivos JSON (SYSTEM.json, OPERATIONS_HISTORY.json, DASHBOARD_CONTEXT.json) publica realmente un export de n8n frente a lo que espera el dashboard, cuando se actualice FLOW_GAP_ANALYSIS.md o N8N_DASHBOARD_EXPORT_SPEC.md, o cuando se valide que el JSON publicado cumple el contrato de datos.
---

# Vcallia Flow Map

Este skill documenta y automatiza la auditoría del pipeline operativo de Vcallia:

```
n8n (n8n2.vcallia.com)
  -> workflow "Dashboard GitHub" (tZy2OwAQAXNo44m8)
    -> PUT a GitHub (este repo)
      -> SYSTEM.json
      -> OPERATIONS_HISTORY.json
      -> DASHBOARD_CONTEXT.json
        -> GitHub Pages
          -> index.html / project.html (dashboard)
```

Úsalo para responder "¿qué hace hoy el flow?", "¿qué le falta publicar?", "¿el JSON que llegó cumple el contrato?" sin releer todo el repo cada vez.

## 1. Las tres fuentes de datos del dashboard

El dashboard (`index.html`, `project.html`) lee tres archivos en la raíz del repo, publicados por el flow de n8n. El contrato completo vive en `N8N_DASHBOARD_EXPORT_SPEC.md` — no lo dupliques, referencia esta tabla resumen:

| Archivo | Rol | Reglas clave |
|---|---|---|
| `SYSTEM.json` | Fuente operativa principal (estado en vivo) | Debe traer `generated_at` del mismo run; incluye `recent_executions`, `workflows`, `hourly_activity` |
| `OPERATIONS_HISTORY.json` | Comparativas día a día | Array `snapshots` ordenado de más reciente a más antiguo; mínimo 7 si el flow puede; un snapshot por día |
| `DASHBOARD_CONTEXT.json` | Contexto humano estructurado | `projects`, `tasks`, `notes`, `changes` |

Regla de publicación no negociable (de `N8N_DASHBOARD_EXPORT_SPEC.md`): **los tres archivos se publican en la misma ejecución**. Si uno falla, se marca el error y no se deja una mezcla de versiones (p. ej. `SYSTEM.json` de las 16:00 con `DASHBOARD_CONTEXT.json` de ayer).

`PROJECTS.md`, `TASKS.md`, `NOTES.md` en la raíz son **solo fallback manual** — el dashboard prioriza el JSON estructurado. No los trates como la fuente de verdad si el JSON existe y es reciente.

## 2. Cómo hacer una auditoría de flow (flow-gap-analysis)

Cuando te den un export de n8n (JSON del workflow, o una lista de nodos) y te pidan auditarlo contra este dashboard:

1. Identifica qué nodos escriben a GitHub (`PUT`/`Actualizar GitHub` sobre qué path).
2. Para cada uno de los 3 archivos de la sección 1, marca: **automatizado** / **pendiente** / **desactualizado**.
3. Si falta alguno, describe en 3-5 bullets qué necesitaría el nodo nuevo (leer SHA actual, decodificar JSON previo, insertar/actualizar, `PUT` con el nuevo SHA) — este es el patrón que ya usa el flow para `SYSTEM.json`.
4. Verifica el orden recomendado (sección "Orden recomendado" de `N8N_DASHBOARD_EXPORT_SPEC.md`): recolectar estado n8n -> construir `SYSTEM.json` -> leer snapshot previo -> insertar snapshot del día -> construir `DASHBOARD_CONTEXT.json` -> publicar -> registrar resultado.
5. Escribe o actualiza `FLOW_GAP_ANALYSIS.md` siguiendo su estructura existente (mismas secciones: `## Lo que hace hoy`, `## Nodos actuales relevantes`, `## Hallazgo principal`, `## Impacto en el dashboard`, `## Ajustes que faltan en n8n`, `## Salida esperada final`, `## Estado actual`). Mantén el tono: español, ASCII plano, sin acentos en encabezados de código/nombres de nodo, listas cortas — no reescribas todo el archivo si solo cambió una sección.

## 3. Validar el JSON publicado

Para comprobar que los tres archivos en la raíz del repo cumplen el contrato (campos requeridos, tipos, orden de snapshots), corre:

```bash
python3 .claude/skills/vcallia-flow-map/scripts/validate_dashboard_json.py
```

Corre sobre `SYSTEM.json`, `OPERATIONS_HISTORY.json` y `DASHBOARD_CONTEXT.json` en la raíz del repo por defecto. Acepta rutas como argumentos si estás validando un export descargado antes de mergear (`--system`, `--history`, `--context`). Ver `references/data-contract.md` para el detalle campo por campo que usa el validador.

## 4. Otras memorias del repo (no confundir con el contrato del dashboard)

`ops-memory/` es un sistema aparte: memoria operativa multi-proyecto (`ops-memory/index.json` + `projects/<id>.json`, `tasks/<id>.json`, `activity/<id>.json`, `requests/projects/*.json`, `requests/workflows/*.json`), alimentado por otro agente (`kimi`) vía webhook (`Ops Memory Sync Webhook` en n8n). No es parte del contrato de `N8N_DASHBOARD_EXPORT_SPEC.md` y no lo debe leer el dashboard directamente — trátalo como un log/planning store independiente salvo que te pidan explícitamente cruzarlo con el flow map.
