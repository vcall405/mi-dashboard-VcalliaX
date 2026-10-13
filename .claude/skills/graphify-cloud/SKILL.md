---
name: graphify-cloud
description: Construye y consulta un grafo de conocimiento de un proyecto de Cloudflare Workers o Google Cloud (Cloud Run, Cloud Functions, App Engine) combinando graphify (llamadas/imports entre funciones, local, 0 tokens) con una capa de infraestructura propia (rutas HTTP, bindings D1/KV/R2/Queues/Durable Objects, crons, vars y secretos de wrangler, topics de Pub/Sub, Firestore, buckets, Secret Manager, env vars y APIs externas como Meta/WhatsApp, n8n, Resend, Vapi). Usar cuando Miguel pida "mapear el proyecto", "qué toca este endpoint", "qué se rompe si cambio X", "de dónde sale este secreto", "cómo llega un webhook hasta Y", antes de refactorizar o desplegar un Worker/servicio, al hacer onboarding de un repo de cliente, o para documentar la arquitectura para un cliente.
---

# graphify-cloud

Grafo de código + infraestructura para los proyectos serverless de Miguel
(Cloudflare Workers y Google Cloud). Pensado para que Claude responda preguntas
de arquitectura **consultando el grafo** en vez de leer archivo por archivo.

## Por qué existe (y qué NO hace graphify solo)

graphify en modo code-only es rápido (~2 s), local y gratis, pero probado en
proyectos reales **no ve**:

- rutas con handler anónimo (`app.post('/webhook', async (c) => ...)` en Hono/Express),
- nada de `wrangler.toml` (bindings, crons, vars, rutas, colas, Durable Objects),
- nada de `cloudbuild.yaml` / `service.yaml` / `app.yaml`,
- a qué topic de Pub/Sub, colección de Firestore, bucket o API externa habla cada función.

`scripts/infra_overlay.py` agrega todo eso al mismo `graph.json`, enganchado a
la función o handler que lo usa, para que `graphify path/explain/affected`
crucen código ↔ infraestructura.

Relación con `vcallia-flow-map`: ese skill mapea **entre servicios y n8n**
(exports JSON de workflows). Este mapea **dentro de un repo** (función a función
hasta el binding, el topic o la API). Para un audit completo, usar ambos.

## Requisitos (una vez por máquina)

```bash
uv tool install graphifyy      # o: pipx install graphifyy  (ojo: doble "y")
```

Probar que todo funciona (incluye un Worker y un proyecto GCP de ejemplo):

```bash
.claude/skills/graphify-cloud/scripts/build.sh .claude/skills/graphify-cloud/examples/cf-worker
cd .claude/skills/graphify-cloud/examples/cf-worker && graphify path "POST /vapi/webhook" "Tabla: llamadas"
# esperado: webhook → Queue CALL_QUEUE → Queue: calls → queue() → saveCall() → Tabla: llamadas
```

No hace falta `graphify install`: este skill usa la CLI directamente.
`graphify install` agrega además hooks de Claude Code que empujan a consultar el
grafo antes de cada búsqueda; si se quiere, instalar solo por proyecto
(`graphify install --project`), nunca global de entrada.

## Flujo

### 1. Construir (o reconstruir) el grafo

```bash
.claude/skills/graphify-cloud/scripts/build.sh <dir_proyecto>
```

Hace: `graphify update --force` → `infra_overlay.py` → `graphify cluster-only --no-label`.
Sin LLM ni API keys. Salida en `<dir>/graphify-out/`:

| Archivo | Para qué |
|---|---|
| `INFRA_MAP.md` | **Leer primero.** Worker/servicio, rutas, bindings, crons, secretos, conexiones con archivo:línea |
| `GRAPH_REPORT.md` | God nodes, comunidades (subsistemas), conexiones sorprendentes |
| `graph.html` | Grafo interactivo (bueno para enseñar a un cliente) |
| `graph.json` | Lo que consultan los comandos de abajo |

**Siempre reconstruir con `build.sh`**, no con `graphify update` a secas: si hubo
cambios de código, `update` solo se niega o borra la capa infra.

### 2. Consultar (desde la raíz del proyecto)

```bash
# ¿cómo llega X hasta Y?  (atraviesa código e infra)
graphify path "POST /vapi/webhook" "Tabla: llamadas"
graphify path "POST /webhook/meta" "Externo: graph.facebook.com"

# todo lo que toca / usa un nodo, con archivo:línea
graphify explain "D1 DB"
graphify explain "saveCall()"

# ¿qué se rompe si cambio X?  (usar impact.sh, NO graphify affected a secas:
#  affected por defecto ignora las relaciones de infraestructura)
.claude/skills/graphify-cloud/scripts/impact.sh "D1 DB" 3
.claude/skills/graphify-cloud/scripts/impact.sh "Secret Manager: meta-token" 4
```

Si un nombre es ambiguo, graphify lo rechaza y lista candidatos (exit 3):
elegir uno y repetir. No adivinar.

### Nombres de los nodos de infraestructura

| Tipo | Etiqueta |
|---|---|
| Worker | `Worker: <name>` |
| Binding | `D1 DB → clientes`, `KV SESSIONS`, `R2 RECORDINGS → bucket`, `Queue CALL_QUEUE → calls`, `DurableObject ROOMS → CallRoom`, `Service AUTH → otro-worker`, `AI AI` |
| Ruta HTTP | `POST /vapi/webhook` · ruta de zona CF: `Ruta CF: api.dominio.com/*` |
| Cron / cola | `Cron: */15 * * * *` · `Queue: calls` |
| Var / secreto CF | `var N8N_WEBHOOK_URL` · `secret VAPI_SECRET` |
| GCP | `Cloud Run: svc` · `Cloud Function: fn` · `App Engine: svc` · `Pub/Sub: topic` · `Firestore: col` · `GCS: bucket` · `BigQuery: dataset` · `Secret Manager: nombre` · `env META_TOKEN` |
| SQL (D1/Postgres en strings) | `Tabla: llamadas` |
| API externa | `Externo: graph.facebook.com` |

## Cómo usarlo bien (para Claude)

1. Al empezar en un repo con `wrangler.toml` o config de GCP: si no existe
   `graphify-out/` o su commit (`Built from commit` en GRAPH_REPORT.md) no es el
   `HEAD` actual, correr `build.sh` antes de explorar.
2. Para preguntas de arquitectura, leer `INFRA_MAP.md` y usar `path`/`explain`/
   `impact.sh` **antes** de abrir archivos. Abrir solo los archivo:línea que el
   grafo señale.
3. Antes de cambiar un binding, un secreto, una tabla o un endpoint: correr
   `impact.sh` y listar al usuario lo afectado.
4. Antes de desplegar: revisar la sección "Secretos usados en código y no
   declarados en wrangler". Cada uno debe existir con `wrangler secret put`; si
   falta, el Worker falla en runtime, no en deploy.
5. Distinguir siempre 🟢 EXTRACTED (literal) de 🟡 INFERRED (deducido) al
   reportar. No presentar un INFERRED como hecho.

## Limitaciones (decirlas cuando importen)

- Detección por patrones: URLs, topics o colecciones construidas dinámicamente
  (`` fetch(`${env.BASE}/x`) ``, `.collection(nombreVar)`) no aparecen. Si se
  usa `fetch(env.X)`, el enlace queda vía la var/secreto `X`.
- Rutas: reconoce receptores `app`, `router`, `server`, `routes` y variables
  creadas con `new Hono()`, `express()`, `Router()`, `AutoRouter()`, `fastify()`;
  `url.pathname === '/x'`, Flask/FastAPI (`@app.get('/x')`). Otros routers no.
- Las tablas SQL se sacan de strings en líneas con `.prepare(` / `.query(` /
  `.exec(` / `` sql` ``: un SQL armado en varias líneas puede perder tablas.
- `wrangler.toml` necesita Python 3.11+ (tomllib) y los YAML de GCP necesitan
  PyYAML. `build.sh` lo resuelve solo si hay `uv`; si no, avisa y sigue.
- Con un solo servicio de Cloud Run/App Engine, sus rutas se enlazan como
  INFERRED (asume que todo el código HTTP del repo es ese servicio).
- graphify no clasifica `.toml`/`.yaml`: por eso esos archivos solo aparecen
  vía esta capa.

## Privacidad

- Todo corre local. El overlay **solo lee nombres** de secretos/vars, nunca
  valores, y no abre `.dev.vars` ni `.env`.
- `graphify-out/` describe la estructura interna del proyecto: en repos de
  cliente agregarlo a `.gitignore` salvo que se quiera versionar a propósito.
- El log de consultas de graphify (0.9.x) está apagado por defecto; se activa
  solo con `GRAPHIFY_QUERY_LOG_ENABLE=1`.
- No usar `graphify extract` (modo semántico con LLM) en repos de cliente sin
  decidirlo: envía docs/PDFs al proveedor del LLM configurado.

## Instalar para todos los proyectos

Este skill vive en este repo. Para usarlo en cualquier proyecto en tu máquina:

```bash
mkdir -p ~/.claude/skills && cp -r .claude/skills/graphify-cloud ~/.claude/skills/
```

Y en los comandos de arriba usar `~/.claude/skills/graphify-cloud/scripts/...`.
