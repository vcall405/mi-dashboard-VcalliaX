---
name: vcallia-flow-map
description: Mapea las conexiones reales entre Cloudflare Workers, workflows de n8n y servicios externos (Twilio, Vapi, WhatsApp/Meta, Cartesia, Resend, Google Maps, D1, KV) leyendo el código y los exports JSON directamente — sin AST genérico, sin vector store, sin dependencias externas. Usar cuando Miguel pida "mapear conexiones", "ver cómo se conecta X con Y", "qué toca este Worker", o antes de tocar un Worker/workflow que no se ha revisado en un tiempo.
---

# Vcallia Flow Map

Skill ligero y propio (no un fork de una herramienta de terceros) para mapear
cómo se conectan los componentes del stack de Miguel: Cloudflare Workers,
workflows de n8n, y los servicios externos que usa en sus proyectos de
automatización (Twilio, Vapi, Cartesia, WhatsApp/Meta, Resend, Google Maps,
D1, KV, Airtable, OpenAI, Anthropic).

## Cuándo usar este skill

- Antes de modificar un Worker o workflow que no se ha tocado en semanas.
- Cuando Miguel pregunte "¿qué se conecta con qué?" en un proyecto.
- Al hacer onboarding de un nuevo Worker o cliente al stack.
- Como primer paso de un audit de seguridad (ver qué secretos/env vars
  toca cada componente).

## Cómo funciona

`scripts/map_connections.py` escanea:

1. **Cloudflare Workers (.js/.mjs)**: busca
   - `fetch("https://...")` con URL literal → conexión **EXTRACTED**
   - `env.ALGO_URL` / `env.ALGO_API_KEY` cuyo nombre coincide con un
     servicio conocido → conexión **INFERRED** (el nombre de la variable
     sugiere el servicio, pero la URL no está fija en ese archivo)
   - `request.headers.get("X-Algo-Secret")` → puntos de entrada autenticados

2. **Exports JSON de n8n** (opcional, con `--n8n`): lee el `type` de cada
   nodo (ej. `n8n-nodes-base.twilio`, `...whatsApp`, `...httpRequest`) y
   el campo `url` de los nodos HTTP Request → conexiones **EXTRACTED**
   porque están explícitas en el JSON del propio workflow.

Cada conexión queda etiquetada EXTRACTED (literal en el código) o INFERRED
(deducida por convención de nombres) — mismo principio que usan las
herramientas de knowledge graph, pero sin instalar nada de terceros ni
mandar código a ningún servidor. Todo corre local.

## Uso

```bash
# Solo Workers de Cloudflare
python3 scripts/map_connections.py ./ruta/a/workers --out ./flow-map-out

# Workers + exports de n8n
python3 scripts/map_connections.py ./ruta/a/workers --n8n ./ruta/a/n8n-exports --out ./flow-map-out
```

Para obtener el código de un Worker ya desplegado en Cloudflare (sin tenerlo
en local), usar la herramienta `workers_get_worker_code` del conector de
Cloudflare, guardar el resultado como `.js`, y correr el script sobre esa
carpeta.

## Salida

- `connections.md` — reporte legible, agrupado por componente, con las
  conexiones salientes y su nivel de confianza.
- `connections.json` — grafo completo (`nodes` + `edges`) para reutilizar
  programáticamente o alimentar una visualización propia más adelante.

## Limitaciones (léelas antes de confiar ciegamente en el resultado)

- Las conexiones **INFERRED** son deducciones por nombre de variable, no
  hechos confirmados — revísalas antes de tomar decisiones de seguridad.
- No sigue redirecciones ni resuelve URLs construidas dinámicamente
  (ej. `` `${env.BASE}/algo` ``) — esas quedan fuera del reporte.
- No analiza n8n en vivo (vía API) todavía, solo exports JSON ya
  descargados. Si se necesita eso, hay que agregar un paso que llame a la
  API de n8n para bajar los workflows primero.
