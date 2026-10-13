#!/usr/bin/env bash
# impact.sh "<nodo>" [profundidad] — qué se rompe si cambio X (código o infra).
# Envuelve `graphify affected` con las relaciones de la capa infra, que por
# defecto graphify no recorre.
set -euo pipefail
NODE="${1:?uso: impact.sh \"<nodo>\" [profundidad]}"
DEPTH="${2:-3}"
RELS=(calls method indirect_call imports imports_from uses_binding reads_table writes_table
      reads_secret reads_var reads_env from_secret handled_by serves sends_to publishes_to
      subscribes_to uses_collection uses_bucket uses_dataset calls_external points_to
      implemented_by calls_worker triggers entrypoint)
ARGS=()
for r in "${RELS[@]}"; do ARGS+=(--relation "$r"); done
graphify affected "$NODE" --depth "$DEPTH" "${ARGS[@]}"
