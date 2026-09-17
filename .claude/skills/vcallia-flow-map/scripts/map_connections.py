#!/usr/bin/env python3
"""
map_connections.py — Mapeador de conexiones para el stack de Vcallia
(Cloudflare Workers + n8n + servicios externos: Twilio, Vapi, WhatsApp/Meta,
Cartesia, Resend, Google Maps, D1, KV).

A diferencia de un grafo genérico de AST (tipo graphify), esto NO intenta
entender "todo el código". Busca patrones específicos de tu stack:

  - env.ALGO_URL / env.ALGO_WEBHOOK_URL / env.ALGO_API_KEY  -> conexión a servicio externo
  - fetch("https://...") con URL literal                     -> conexión EXTRACTED directa
  - request.headers.get("X-Algo-Secret")                     -> punto de entrada autenticado
  - nodos de un export JSON de n8n (n8n-nodes-base.httpRequest,
    twilio, whatsApp, webhook, etc.) y sus conexiones internas

Cada conexión se etiqueta:
  EXTRACTED  -> literalmente está en el código/JSON (URL, nombre de nodo, tipo)
  INFERRED   -> se dedujo por convención de nombres (ej: env var contiene
                "WHATSAPP" o "VAPI" pero no hay URL literal en ese archivo)

Uso:
  python3 map_connections.py <carpeta_o_archivo> [--n8n <carpeta_exports_n8n>] --out <carpeta_salida>

Sin dependencias externas — solo stdlib.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Palabras clave de servicios conocidos en el stack de Vcallia
SERVICE_KEYWORDS = {
    "TWILIO": "Twilio (voz/SMS/PBX)",
    "VAPI": "Vapi (agente de voz)",
    "CARTESIA": "Cartesia (TTS)",
    "WHATSAPP": "WhatsApp / Meta API",
    "RESEND": "Resend (email)",
    "SENDGRID": "SendGrid (email)",
    "GOOGLE_MAPS": "Google Maps API",
    "AIRTABLE": "Airtable",
    "OPENAI": "OpenAI",
    "ANTHROPIC": "Anthropic Claude",
    "D1": "Cloudflare D1 (DB)",
    "KV": "Cloudflare KV",
    "ENCRYPTION": "Cifrado interno (AES-GCM)",
}

ENV_VAR_RE = re.compile(r"env\.([A-Z0-9_]+)")
FETCH_LITERAL_RE = re.compile(r"fetch\(\s*[\"'`](https?://[^\"'`\s]+)[\"'`]")
HEADER_SECRET_RE = re.compile(r"headers\.get\(\s*[\"']([A-Za-z0-9-]+)[\"']\s*\)")
ROUTE_RE = re.compile(r"url\.pathname\s*===\s*[\"']([^\"']+)[\"']")


def classify_env_var(name: str):
    for kw, label in SERVICE_KEYWORDS.items():
        if kw in name:
            return label
    return None


def scan_worker_file(path: Path):
    """Escanea un archivo de Cloudflare Worker (.js) y devuelve nodos y edges."""
    text = path.read_text(errors="ignore")
    worker_name = path.stem
    edges = []
    routes = []

    for m in ROUTE_RE.finditer(text):
        routes.append(m.group(1))

    for m in FETCH_LITERAL_RE.finditer(text):
        url = m.group(1)
        edges.append({
            "from": worker_name,
            "to": url,
            "type": "http_call",
            "confidence": "EXTRACTED",
            "evidence": f"fetch() literal en {path.name}",
        })

    seen_env = set()
    for m in ENV_VAR_RE.finditer(text):
        var = m.group(1)
        if var in seen_env:
            continue
        seen_env.add(var)
        label = classify_env_var(var)
        if label:
            edges.append({
                "from": worker_name,
                "to": label,
                "type": "env_binding",
                "confidence": "INFERRED",
                "evidence": f"env.{var} referenciado en {path.name}",
            })

    secrets = set(HEADER_SECRET_RE.findall(text))

    return {
        "id": worker_name,
        "kind": "cloudflare_worker",
        "file": str(path),
        "routes": routes,
        "auth_headers_checked": sorted(secrets),
    }, edges


def scan_n8n_export(path: Path):
    """Escanea un export JSON de un workflow de n8n."""
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return None, []

    workflow_name = data.get("name", path.stem)
    nodes = data.get("nodes", [])
    node_by_name = {n.get("name"): n for n in nodes}
    edges = []
    external_nodes = []

    for n in nodes:
        ntype = n.get("type", "")
        nname = n.get("name", "unknown")
        label = None
        # n8n node types traen el nombre del servicio en el propio "type"
        for kw, human in SERVICE_KEYWORDS.items():
            if kw.lower() in ntype.lower():
                label = human
                break
        if label:
            external_nodes.append(nname)
            edges.append({
                "from": f"n8n:{workflow_name}",
                "to": label,
                "type": "n8n_node",
                "confidence": "EXTRACTED",
                "evidence": f"nodo '{nname}' (type={ntype}) en {path.name}",
            })
        # HTTP Request nodes con URL fija en los parámetros
        params = n.get("parameters", {})
        url = params.get("url")
        if isinstance(url, str) and url.startswith("http"):
            edges.append({
                "from": f"n8n:{workflow_name}",
                "to": url,
                "type": "http_call",
                "confidence": "EXTRACTED",
                "evidence": f"nodo '{nname}' HTTP Request en {path.name}",
            })

    return {
        "id": f"n8n:{workflow_name}",
        "kind": "n8n_workflow",
        "file": str(path),
        "node_count": len(nodes),
        "external_nodes": external_nodes,
    }, edges


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="Carpeta o archivo de Cloudflare Workers (.js)")
    ap.add_argument("--n8n", help="Carpeta con exports JSON de workflows de n8n", default=None)
    ap.add_argument("--out", help="Carpeta de salida", default="./flow-map-out")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes = []
    all_edges = []

    target = Path(args.path)
    worker_files = [target] if target.is_file() else list(target.rglob("*.js")) + list(target.rglob("*.mjs"))
    for f in worker_files:
        node, edges = scan_worker_file(f)
        nodes.append(node)
        all_edges.extend(edges)

    if args.n8n:
        n8n_dir = Path(args.n8n)
        for f in n8n_dir.rglob("*.json"):
            node, edges = scan_n8n_export(f)
            if node:
                nodes.append(node)
                all_edges.extend(edges)

    graph = {"nodes": nodes, "edges": all_edges}
    (out_dir / "connections.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    # Reporte legible
    lines = ["# Mapa de conexiones — Vcallia stack\n"]
    lines.append(f"Nodos escaneados: {len(nodes)}  |  Conexiones encontradas: {len(all_edges)}\n")
    for node in nodes:
        lines.append(f"\n## {node['id']}  ({node['kind']})")
        lines.append(f"Archivo: `{node['file']}`")
        if node["kind"] == "cloudflare_worker" and node.get("routes"):
            lines.append(f"Rutas expuestas: {', '.join(node['routes'])}")
        node_edges = [e for e in all_edges if e["from"] == node["id"]]
        if node_edges:
            lines.append("\nConexiones salientes:")
            for e in node_edges:
                tag = "🟢 EXTRACTED" if e["confidence"] == "EXTRACTED" else "🟡 INFERRED"
                lines.append(f"  - {tag} → **{e['to']}**  _(via {e['evidence']})_")
    (out_dir / "connections.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"OK: {len(nodes)} nodos, {len(all_edges)} conexiones -> {out_dir}/connections.md y connections.json")


if __name__ == "__main__":
    main()
