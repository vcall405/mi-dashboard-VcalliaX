#!/usr/bin/env bash
# graphify-cloud: grafo de código (graphify, local, 0 tokens) + capa de infraestructura
# (Cloudflare Workers / Google Cloud) + re-clustering y reporte.
#
# Uso: build.sh [dir_proyecto]   (default: .)
set -euo pipefail

DIR="${1:-.}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v graphify >/dev/null 2>&1; then
  echo "graphify no está instalado. Instálalo con:  uv tool install graphifyy   (o: pipx install graphifyy)" >&2
  exit 1
fi

# 1) grafo de código. --force porque la capa infra de la corrida anterior hace que el
#    rebuild tenga "menos nodos" y graphify se negaría a sobrescribir.
graphify update "$DIR" --force

# 2) capa infra. Necesita Python 3.11+ (tomllib) y PyYAML; con uv se resuelve solo.
if command -v uv >/dev/null 2>&1; then
  uv run --quiet --no-project --python '>=3.11' --with pyyaml python "$HERE/infra_overlay.py" "$DIR"
else
  python3 "$HERE/infra_overlay.py" "$DIR"
fi

# 3) re-clusteriza con los nodos nuevos y regenera graph.html + GRAPH_REPORT.md (sin LLM)
graphify cluster-only "$DIR" --no-label

echo
echo "Listo: $DIR/graphify-out/{GRAPH_REPORT.md,INFRA_MAP.md,graph.html,graph.json}"
