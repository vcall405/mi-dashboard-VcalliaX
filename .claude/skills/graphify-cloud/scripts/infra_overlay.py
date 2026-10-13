#!/usr/bin/env python3
"""
infra_overlay.py — Capa de infraestructura para un grafo de graphify.

graphify (modo code-only) entiende llamadas e imports entre funciones, pero NO
ve lo que hace funcionar un proyecto serverless:

  Cloudflare Workers: rutas, bindings (D1, KV, R2, Queues, Durable Objects,
                      service bindings, AI...), vars, secretos, crons.
  Google Cloud:       servicios de Cloud Run, Cloud Functions y su trigger,
                      topics de Pub/Sub, colecciones de Firestore, buckets,
                      secretos de Secret Manager, variables de entorno.
  Ambos:              rutas HTTP con handler anónimo y hosts externos llamados
                      con fetch/requests/axios (Meta, n8n, Resend, Vapi...).

Este script lee la config real (wrangler.toml/.jsonc, cloudbuild.yaml,
service.yaml, app.yaml) y el código, y AGREGA esos nodos y edges al
graphify-out/graph.json existente, enganchados a la función que los usa.
Así `graphify path`, `explain` y `affected` cruzan código <-> infraestructura.

Confianza (misma convención que graphify):
  EXTRACTED  literal en config o código (binding declarado, ruta, topic, URL)
  INFERRED   deducido (env.X sin declarar en wrangler => secreto; servicio de
             Cloud Run => rutas del proyecto cuando hay uno solo)

Nunca lee valores de secretos: solo nombres. No lee .dev.vars ni .env.

Uso:
  python3 infra_overlay.py <dir_proyecto> [--graph RUTA] [--report RUTA]

Normalmente se llama desde build.sh (update -> overlay -> cluster-only).
Es idempotente: borra la capa anterior (_origin == "infra") antes de escribir.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

ORIGIN = "infra"
CODE_EXT = {".js", ".mjs", ".cjs", ".ts", ".mts", ".tsx", ".jsx", ".py"}
SKIP_DIRS = {"node_modules", ".git", "graphify-out", "dist", "build", ".wrangler",
             ".next", ".venv", "venv", "__pycache__", ".vercel", "coverage"}

# Tipos de binding de wrangler -> (clave en config, campo con el nombre, campo con el recurso, etiqueta)
CF_BINDINGS = [
    ("kv_namespaces", "binding", None, "KV"),
    ("d1_databases", "binding", "database_name", "D1"),
    ("r2_buckets", "binding", "bucket_name", "R2"),
    ("vectorize", "binding", "index_name", "Vectorize"),
    ("hyperdrive", "binding", None, "Hyperdrive"),
    ("analytics_engine_datasets", "binding", "dataset", "AnalyticsEngine"),
    ("services", "binding", "service", "Service"),
    ("workflows", "binding", "name", "Workflow"),
]


# ----------------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------------
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "x"


def strip_jsonc(text: str) -> str:
    """Quita comentarios // y /* */ y comas finales de un JSONC (respeta strings)."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def line_of(text: str, needle: str, default: int = 1) -> int:
    idx = text.find(needle)
    return default if idx == -1 else text.count("\n", 0, idx) + 1


def js_block_end(lines: list[str], start: int, opener: str = "{") -> int:
    """Desde la línea `start` (1-based) busca el primer `opener` y devuelve la
    línea donde se cierra, saltando strings y comentarios. Aproximado a propósito."""
    closer = {"{": "}", "(": ")"}[opener]
    depth, seen = 0, False
    in_str: str | None = None
    in_block_comment = False
    prev = "("  # último carácter significativo, para distinguir regex de división
    for ln in range(start, len(lines) + 1):
        s = lines[ln - 1]
        i = 0
        while i < len(s):
            c = s[i]
            if not in_str and not in_block_comment and c == "/" and not s.startswith(("//", "/*"), i) \
                    and prev in "(,=:[!&|?{};+-*%~^":  # sin < > para no confundir JSX </div>
                # regex literal: salta hasta la / de cierre (fuera de [...])
                i += 1
                in_class = False
                while i < len(s):
                    if s[i] == "\\":
                        i += 1
                    elif s[i] == "[":
                        in_class = True
                    elif s[i] == "]":
                        in_class = False
                    elif s[i] == "/" and not in_class:
                        break
                    i += 1
                prev = "x"
                i += 1
                continue
            if not in_str and not in_block_comment and not c.isspace():
                prev = c
            if in_block_comment:
                if s.startswith("*/", i):
                    in_block_comment = False
                    i += 1
            elif in_str:
                if c == "\\":
                    i += 1
                elif c == in_str:
                    in_str = None
            elif s.startswith("//", i):
                break
            elif s.startswith("/*", i):
                in_block_comment = True
                i += 1
            elif c in "'\"`":
                in_str = c
            elif c == opener:
                depth += 1
                seen = True
            elif c == closer and seen:
                depth -= 1
                if depth == 0:
                    return ln
            i += 1
        if in_str in ("'", '"'):  # strings simples no cruzan líneas
            in_str = None
    return len(lines)


def py_block_end(lines: list[str], start: int) -> int:
    head = lines[start - 1]
    indent = len(head) - len(head.lstrip())
    end = start
    for ln in range(start + 1, len(lines) + 1):
        s = lines[ln - 1]
        if not s.strip():
            continue
        if len(s) - len(s.lstrip()) <= indent and not s.lstrip().startswith((")", "]", "}")):
            break
        end = ln
    return end


def git_prefix(root: Path) -> str:
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        rel = root.resolve().relative_to(Path(top).resolve()).as_posix()
        return "" if rel == "." else rel + "/"
    except Exception:  # noqa: BLE001  (sin git o fuera de un repo)
        return ""


def parse_loc(loc) -> int | None:
    m = re.match(r"L(\d+)", str(loc or ""))
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# el overlay
# ----------------------------------------------------------------------------
class Overlay:
    def __init__(self, root: Path, graph: dict):
        self.root = root
        self.graph = graph
        self.nodes: dict[str, dict] = {}
        self.links: list[dict] = []
        self._link_keys: set[tuple] = set()
        self.warnings: list[str] = []
        # índices sobre el grafo de código
        self.code_nodes = [n for n in graph["nodes"] if n.get("_origin") != ORIGIN]
        self.by_file: dict[str, list[dict]] = defaultdict(list)
        self.file_node: dict[str, str] = {}
        # En un subdirectorio de un repo (monorepo), graphify guarda rutas desde la
        # raíz de git: las normalizamos a rutas relativas al proyecto.
        self.prefix = git_prefix(root)
        for n in self.code_nodes:
            sf = n.get("source_file")
            if not sf:
                continue
            if self.prefix and sf.startswith(self.prefix):
                sf = sf[len(self.prefix):]
            self.by_file[sf].append(n)
            if n.get("label") == Path(sf).name:
                self.file_node[sf] = n["id"]
        # nombre -> id de funciones top-level (solo nombres únicos, para no adivinar)
        counts: dict[str, list[str]] = defaultdict(list)
        for n in self.code_nodes:
            lab = n.get("label", "")
            if n.get("_callable") and lab.endswith("()") and not lab.startswith("."):
                counts[lab[:-2]].append(n["id"])
        self.callable_by_name = {k: v[0] for k, v in counts.items() if len(v) == 1}
        self.ranges: dict[str, list[tuple[int, int, str]]] = {}
        # resultados para el reporte
        self.cf_workers: dict[str, dict] = {}
        self.cf_bindings: dict[str, str] = {}   # NOMBRE -> node id
        self.cf_vars: dict[str, str] = {}
        self.gcp_secret_env: dict[str, str] = {}  # ENV -> secret node id
        self.gcp_services: list[str] = []

    # --- construcción de nodos/edges ---------------------------------------
    def node(self, kind: str, name: str, label: str, source_file: str = "", line: int = 1, **extra) -> str:
        nid = f"infra_{slug(kind)}_{slug(name)}"
        if nid not in self.nodes:
            self.nodes[nid] = {
                "id": nid, "label": label, "_origin": ORIGIN, "file_type": "infra",
                "infra_kind": kind, "norm_label": label.lower(),
                "source_file": self.prefix + source_file if source_file else "", "source_location": f"L{line}", **extra,
            }
        return nid

    def link(self, src: str, dst: str, relation: str, confidence: str, source_file: str, line: int, context: str = "infra"):
        key = (src, dst, relation)
        if key in self._link_keys or src == dst:
            return
        self._link_keys.add(key)
        self.links.append({
            "source": src, "target": dst, "relation": relation, "_origin": ORIGIN,
            "confidence": confidence, "confidence_score": 1.0 if confidence == "EXTRACTED" else 0.7,
            "context": context, "source_file": self.prefix + source_file if source_file else "",
            "source_location": f"L{line}", "weight": 1.0,
        })

    # --- quién "posee" una línea de código -----------------------------------
    def owner(self, rel: str, line: int) -> str | None:
        best = None
        for start, end, nid in self.ranges.get(rel, []):
            if start <= line <= end and (best is None or end - start < best[1] - best[0]):
                best = (start, end, nid)
        return best[2] if best else self.file_node.get(rel)

    def build_ranges(self, rel: str, text: str):
        lines = text.splitlines()
        is_py = rel.endswith(".py")
        out = []
        for n in self.by_file.get(rel, []):
            if not n.get("_callable"):
                continue
            start = parse_loc(n.get("source_location"))
            if not start or start > len(lines):
                continue
            if is_py:
                # el nodo puede apuntar al decorador; avanza hasta el def/class
                s = start
                while s <= len(lines) and lines[s - 1].lstrip().startswith("@"):
                    s += 1
                end = py_block_end(lines, min(s, len(lines)))
            else:
                end = js_block_end(lines, start, "{")
            out.append((start, end, n["id"]))
        self.ranges[rel] = out

    # --- recorrido de archivos ----------------------------------------------
    def iter_files(self, exts: set[str] | None = None, names: set[str] | None = None):
        for p in self.root.rglob("*"):
            if any(part in SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            if not p.is_file():
                continue
            if exts and p.suffix in exts or names and p.name in names:
                yield p

    def local(self, sf: str) -> str:
        return sf[len(self.prefix):] if self.prefix and sf.startswith(self.prefix) else sf

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ========================================================================
    # Cloudflare
    # ========================================================================
    def load_wrangler(self, p: Path) -> dict | None:
        text = p.read_text(errors="ignore")
        try:
            if p.suffix == ".toml":
                if tomllib is None:
                    self.warnings.append(f"{self.rel(p)}: falta tomllib (usa Python 3.11+ o instala tomli)")
                    return None
                return tomllib.loads(text)
            return json.loads(strip_jsonc(text))
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"{self.rel(p)}: no se pudo leer ({e})")
            return None

    def cloudflare(self):
        configs = list(self.iter_files(names={"wrangler.toml", "wrangler.json", "wrangler.jsonc"}))
        for p in configs:
            cfg = self.load_wrangler(p)
            if not cfg:
                continue
            rel, text = self.rel(p), p.read_text(errors="ignore")
            name = cfg.get("name") or p.parent.name
            w = self.node("worker", name, f"Worker: {name}", rel, line_of(text, "name"))
            info = self.cf_workers.setdefault(name, {"config": rel, "routes": [], "bindings": [], "crons": [],
                                                    "vars": [], "main": cfg.get("main")})
            # entorno base + env.<x>
            envs = [("", cfg)] + [(k, v) for k, v in (cfg.get("env") or {}).items() if isinstance(v, dict)]
            for env_name, c in envs:
                tag = f" [{env_name}]" if env_name else ""
                # rutas públicas
                routes = c.get("routes") or ([c["route"]] if c.get("route") else [])
                for r in routes:
                    pat = r.get("pattern") if isinstance(r, dict) else str(r)
                    if pat:
                        rn = self.node("cf_route", pat, f"Ruta CF: {pat}", rel, line_of(text, pat))
                        self.link(rn, w, "routes_to", "EXTRACTED", rel, line_of(text, pat))
                        info["routes"].append(pat + tag)
                # crons
                for cron in (c.get("triggers") or {}).get("crons", []) or []:
                    cn = self.node("cron", f"{name}_{cron}", f"Cron: {cron}", rel, line_of(text, cron))
                    self.link(cn, w, "triggers", "EXTRACTED", rel, line_of(text, cron))
                    info["crons"].append(cron + tag)
                # vars (solo nombres; si el valor es URL, enlaza el host)
                for k, v in (c.get("vars") or {}).items():
                    vn = self.node("cf_var", f"{name}_{k}", f"var {k}", rel, line_of(text, k))
                    self.cf_vars[k] = vn
                    self.link(w, vn, "declares", "EXTRACTED", rel, line_of(text, k))
                    if isinstance(v, str) and v.startswith("http"):
                        host = re.sub(r"^https?://", "", v).split("/")[0]
                        self.link(vn, self.host_node(host), "points_to", "EXTRACTED", rel, line_of(text, v))
                    if k not in info["vars"]:
                        info["vars"].append(k)
                # bindings simples
                for key, bfield, rfield, label in CF_BINDINGS:
                    for b in c.get(key) or []:
                        self.add_cf_binding(w, name, label, b.get(bfield), b.get(rfield), rel, text, info)
                        if label == "Service" and b.get("service"):
                            other = self.node("worker", b["service"], f"Worker: {b['service']}", rel, line_of(text, b["service"]))
                            self.link(self.cf_bindings[b[bfield]], other, "calls_worker", "EXTRACTED", rel, line_of(text, b["service"]))
                # singletons
                for key, label in (("ai", "AI"), ("browser", "Browser"), ("images", "Images")):
                    if isinstance(c.get(key), dict) and c[key].get("binding"):
                        self.add_cf_binding(w, name, label, c[key]["binding"], None, rel, text, info)
                # Durable Objects
                for b in (c.get("durable_objects") or {}).get("bindings", []) or []:
                    bn = self.add_cf_binding(w, name, "DurableObject", b.get("name"), b.get("class_name"), rel, text, info)
                    cls = self.find_code_node(b.get("class_name"))
                    if bn and cls:
                        self.link(bn, cls, "implemented_by", "EXTRACTED", rel, line_of(text, b.get("class_name", "")))
                # Queues
                q = c.get("queues") or {}
                for b in q.get("producers", []) or []:
                    bn = self.add_cf_binding(w, name, "Queue", b.get("binding"), b.get("queue"), rel, text, info)
                    qn = self.node("queue", b.get("queue", "?"), f"Queue: {b.get('queue')}", rel, line_of(text, b.get("queue", "")))
                    if bn:
                        self.link(bn, qn, "sends_to", "EXTRACTED", rel, line_of(text, b.get("queue", "")))
                for b in q.get("consumers", []) or []:
                    qn = self.node("queue", b.get("queue", "?"), f"Queue: {b.get('queue')}", rel, line_of(text, b.get("queue", "")))
                    self.link(qn, w, "consumed_by", "EXTRACTED", rel, line_of(text, "consumers"))
            # enlazar handlers exportados (fetch/scheduled/queue/email) del entrypoint
            self.link_worker_handlers(name, w, cfg.get("main"), p.parent)

    def add_cf_binding(self, w, worker, label, bname, resource, rel, text, info):
        if not bname:
            return None
        res = f" → {resource}" if resource and resource != bname else ""
        bn = self.node("cf_binding", f"{worker}_{bname}", f"{label} {bname}{res}", rel,
                       line_of(text, f'"{bname}"', line_of(text, bname)), binding_type=label)
        self.cf_bindings[bname] = bn
        self.link(w, bn, "declares", "EXTRACTED", rel, line_of(text, bname))
        entry = f"{label} {bname}{res}"
        if entry not in info["bindings"]:
            info["bindings"].append(entry)
        return bn

    def link_worker_handlers(self, name, w, main, base: Path):
        if not main:
            return
        main_rel = (base / main).resolve().relative_to(self.root.resolve()).as_posix() if (base / main).exists() else None
        if not main_rel:
            self.warnings.append(f"Worker {name}: no encuentro main={main}")
            return
        crons = [nid for nid, n in self.nodes.items() if n["infra_kind"] == "cron" and any(
            l["source"] == nid and l["target"] == w for l in self.links)]
        queues = [l["source"] for l in self.links if l["target"] == w and l["relation"] == "consumed_by"]
        for n in self.by_file.get(main_rel, []):
            lab = n.get("label", "")
            if lab == "scheduled()":
                for c in crons:
                    self.link(c, n["id"], "handled_by", "EXTRACTED", main_rel, parse_loc(n.get("source_location")) or 1)
            elif lab == "queue()":
                for q in queues:
                    self.link(q, n["id"], "handled_by", "EXTRACTED", main_rel, parse_loc(n.get("source_location")) or 1)
        fn = self.file_node.get(main_rel)
        if fn:
            self.link(w, fn, "entrypoint", "EXTRACTED", main_rel, 1)

    def find_code_node(self, label: str | None) -> str | None:
        if not label:
            return None
        for n in self.code_nodes:
            if n.get("label") in (label, f"{label}()"):
                return n["id"]
        return None

    def host_node(self, host: str) -> str:
        return self.node("external", host, f"Externo: {host}")

    # ========================================================================
    # Google Cloud
    # ========================================================================
    def load_yaml_docs(self, p: Path) -> list:
        if yaml is None:
            self.warnings.append(f"{self.rel(p)}: falta PyYAML, config omitida (build.sh lo resuelve con uv)")
            return []
        try:
            return [d for d in yaml.safe_load_all(p.read_text(errors="ignore")) if d]
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"{self.rel(p)}: YAML inválido ({e})")
            return []

    def gcp(self):
        for p in self.iter_files(names={"cloudbuild.yaml", "cloudbuild.yml"}):
            rel, text = self.rel(p), p.read_text(errors="ignore")
            for doc in self.load_yaml_docs(p):
                for step in doc.get("steps", []) or []:
                    args = step.get("args") or []
                    if isinstance(args, str):
                        args = shlex.split(args)
                    if step.get("entrypoint") in ("bash", "sh") and args:
                        joined = " ".join(map(str, args))
                        m = re.search(r"gcloud\s+(.*)", joined)
                        args = shlex.split(m.group(1)) if m else []
                    self.gcloud_cmd([str(a) for a in args], rel, text)
        for p in self.iter_files(exts={".yaml", ".yml"}):
            if p.name.startswith("cloudbuild"):
                continue
            rel, text = self.rel(p), p.read_text(errors="ignore")
            if "serving.knative.dev" not in text and p.name not in ("app.yaml", "app.yml"):
                continue
            for doc in self.load_yaml_docs(p):
                if doc.get("kind") == "Service" and "serving.knative.dev" in str(doc.get("apiVersion")):
                    self.cloud_run_yaml(doc, rel, text)
                elif "runtime" in doc:
                    self.app_engine_yaml(doc, p, rel, text)

    def gcloud_cmd(self, a: list[str], rel: str, text: str):
        def opt(flag):
            for i, x in enumerate(a):
                if x == flag and i + 1 < len(a):
                    return a[i + 1]
                if x.startswith(flag + "="):
                    return x.split("=", 1)[1]
            return None

        if a[:2] == ["run", "deploy"] and len(a) > 2:
            svc = a[2]
            sn = self.node("cloud_run", svc, f"Cloud Run: {svc}", rel, line_of(text, svc))
            self.gcp_services.append(sn)
            for pair in (opt("--set-secrets") or opt("--update-secrets") or "").split(","):
                if "=" in pair:
                    env, sec = pair.split("=", 1)
                    self.secret_env(sn, env, sec.split(":")[0], rel, line_of(text, pair))
        elif a[:2] == ["functions", "deploy"] and len(a) > 2:
            fname = a[2]
            fn = self.node("cloud_function", fname, f"Cloud Function: {fname}", rel, line_of(text, fname))
            entry = opt("--entry-point") or fname
            src = (opt("--source") or ".").strip("./")
            code = self.find_entry(entry, src)
            if code:
                self.link(fn, code, "entrypoint", "EXTRACTED", rel, line_of(text, entry))
            else:
                self.warnings.append(f"Cloud Function {fname}: no encuentro entry-point {entry}() en '{src or '.'}'")
            topic = opt("--trigger-topic")
            if topic:
                tn = self.node("pubsub_topic", topic, f"Pub/Sub: {topic}", rel, line_of(text, topic))
                self.link(tn, fn, "triggers", "EXTRACTED", rel, line_of(text, topic))
            bucket = opt("--trigger-bucket")
            if bucket:
                bn = self.node("gcs_bucket", bucket, f"GCS: {bucket}", rel, line_of(text, bucket))
                self.link(bn, fn, "triggers", "EXTRACTED", rel, line_of(text, bucket))
            if "--trigger-http" in a:
                self.link(self.host_node("cloudfunctions (HTTP público)"), fn, "triggers", "EXTRACTED", rel, line_of(text, "--trigger-http"))
            for pair in (opt("--set-secrets") or "").split(","):
                if "=" in pair:
                    env, sec = pair.split("=", 1)
                    self.secret_env(fn, env, sec.split(":")[0], rel, line_of(text, pair))

    def find_entry(self, entry: str, src_dir: str) -> str | None:
        for n in self.code_nodes:
            if n.get("label") in (f"{entry}()", entry) and n.get("_callable"):
                if not src_dir or self.local(str(n.get("source_file", ""))).startswith(src_dir):
                    return n["id"]
        return None

    def secret_env(self, owner: str, env: str, secret: str, rel: str, line: int):
        sn = self.node("secret", secret, f"Secret Manager: {secret}", rel, line)
        en = self.node("env", env, f"env {env}", rel, line)
        self.link(owner, en, "declares", "EXTRACTED", rel, line)
        self.link(en, sn, "from_secret", "EXTRACTED", rel, line)
        self.gcp_secret_env[env] = sn

    def cloud_run_yaml(self, doc: dict, rel: str, text: str):
        svc = (doc.get("metadata") or {}).get("name", "service")
        sn = self.node("cloud_run", svc, f"Cloud Run: {svc}", rel, line_of(text, svc))
        if sn not in self.gcp_services:
            self.gcp_services.append(sn)
        for c in (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers", []) or []:
            for e in c.get("env", []) or []:
                ref = ((e.get("valueFrom") or {}).get("secretKeyRef") or {}).get("name")
                if ref:
                    self.secret_env(sn, e["name"], ref, rel, line_of(text, e["name"]))
                else:
                    en = self.node("env", e["name"], f"env {e['name']}", rel, line_of(text, e["name"]))
                    self.link(sn, en, "declares", "EXTRACTED", rel, line_of(text, e["name"]))

    def app_engine_yaml(self, doc: dict, p: Path, rel: str, text: str):
        svc = doc.get("service", "default")
        sn = self.node("app_engine", svc, f"App Engine: {svc} ({doc.get('runtime')})", rel, 1)
        self.gcp_services.append(sn)
        for k in (doc.get("env_variables") or {}):
            en = self.node("env", k, f"env {k}", rel, line_of(text, k))
            self.link(sn, en, "declares", "EXTRACTED", rel, line_of(text, k))
        for h in doc.get("handlers", []) or []:
            if h.get("url"):
                rn = self.node("route", f"ae_{h['url']}", f"Ruta AE: {h['url']}", rel, line_of(text, h["url"]))
                self.link(sn, rn, "serves", "EXTRACTED", rel, line_of(text, h["url"]))

    # ========================================================================
    # código (JS/TS/Python)
    # ========================================================================
    ROUTE_JS = re.compile(r"\b(\w+)\.(get|post|put|patch|delete|all|options|head)\(\s*['\"`](/[^'\"`$]*)['\"`]")
    # receptores que son routers de servidor (no clientes HTTP como axios/api)
    ROUTER_DECL = re.compile(r"\b(\w+)\s*=\s*(?:new\s+(?:Hono|OpenAPIHono|Router|Elysia)\b|express\(\)|express\.Router\(|Router\(|"
                             r"AutoRouter\(|IttyRouter\(|fastify\(|Fastify\(|createRouter\()")
    ROUTER_DEFAULT = {"app", "router", "server", "routes"}
    ROUTE_PATHNAME = re.compile(r"pathname\s*===?\s*['\"](/[^'\"]*)['\"]")
    ROUTE_PY = re.compile(r"@\w+\.(route|get|post|put|patch|delete)\(\s*['\"](/[^'\"]*)['\"]")
    CF_ENV = re.compile(r"(?<![\w.])(?:c\.|this\.|ctx\.)?env\.([A-Z][A-Z0-9_]*)\b")
    PROC_ENV = re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)|os\.environ(?:\.get)?[\[(]\s*['\"]([A-Z][A-Z0-9_]*)['\"]|os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]")
    TOPIC = re.compile(r"\.topic\(\s*['\"]([\w.-]+)['\"]|topic_path\([^,]+,\s*['\"]([\w.-]+)['\"]")
    SUBSCRIPTION = re.compile(r"\.subscription\(\s*['\"]([\w.-]+)['\"]|subscription_path\([^,]+,\s*['\"]([\w.-]+)['\"]")
    COLLECTION = re.compile(r"\.collection\(\s*['\"]([\w.-]+)['\"]")
    BUCKET = re.compile(r"\.bucket\(\s*['\"]([\w.-]+)['\"]")
    DATASET = re.compile(r"\.dataset\(\s*['\"]([\w.-]+)['\"]")
    SECRET_MGR = re.compile(r"secrets/([\w-]+)/versions")
    HTTP_LIT = re.compile(r"(?:fetch|axios(?:\.\w+)?|requests\.\w+|httpx\.\w+|got(?:\.\w+)?)\(\s*f?['\"`]https?://([^/'\"`\s$]+)")
    SQL = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+[`\"]?([A-Za-z_][\w]*)", re.I)
    CALL = re.compile(r"(?<![\w.])(\w+)\(")
    SQL_HINT = re.compile(r"\.prepare\(|\bsql`|\.exec\(|\.query\(")

    def code(self):
        has_cf = bool(self.cf_workers)
        for p in self.iter_files(exts=CODE_EXT):
            rel = self.rel(p)
            text = p.read_text(errors="ignore")
            self.build_ranges(rel, text)
            lines = text.splitlines()
            route_ranges = []
            routers = self.ROUTER_DEFAULT | set(self.ROUTER_DECL.findall(text))
            if re.search(r"\b(axios|fetch)\b", text) and not self.ROUTER_DECL.search(text):
                routers = set()  # archivo cliente (frontend): sus .get('/x') no son rutas
            # 1) rutas -> nodo de ruta; el handler anónimo pasa a ser "dueño" de su bloque
            for ln, s in enumerate(lines, 1):
                for m in self.ROUTE_JS.finditer(s):
                    if m.group(1) not in routers:
                        continue
                    rn = self.route(m.group(2).upper(), m.group(3), rel, ln)
                    end = js_block_end(lines, ln, "(")
                    route_ranges.append((ln, end, rn))
                for m in self.ROUTE_PATHNAME.finditer(s):
                    rn = self.route("ANY", m.group(1), rel, ln)
                    own = self.owner(rel, ln)
                    if own:
                        self.link(rn, own, "handled_by", "EXTRACTED", rel, ln)
                for m in self.ROUTE_PY.finditer(s):
                    verb = "ANY" if m.group(1) == "route" else m.group(1).upper()
                    rn = self.route(verb, m.group(2), rel, ln)
                    fn = self.owner(rel, ln + 1) or self.owner(rel, ln)
                    if fn:
                        self.link(rn, fn, "handled_by", "EXTRACTED", rel, ln)
            self.ranges[rel] = self.ranges.get(rel, []) + route_ranges
            # graphify no ve las llamadas hechas dentro de handlers anónimos: las agregamos
            for start, end, rn in route_ranges:
                for ln in range(start, end + 1):
                    for m in self.CALL.finditer(lines[ln - 1]):
                        target = self.callable_by_name.get(m.group(1))
                        if target:
                            self.link(rn, target, "calls", "EXTRACTED", rel, ln)
            # 2) usos dentro de funciones/handlers
            for ln, s in enumerate(lines, 1):
                stripped = s.lstrip()
                if stripped.startswith(("//", "#", "*")):
                    continue
                own = self.owner(rel, ln)
                if not own:
                    continue
                if has_cf:
                    for m in self.CF_ENV.finditer(s):
                        if s[max(0, m.start() - 8):m.start()].endswith("process."):
                            continue
                        self.cf_env_use(own, m.group(1), rel, ln)
                for m in self.PROC_ENV.finditer(s):
                    name = next(g for g in m.groups() if g)
                    en = self.node("env", name, f"env {name}", rel, ln)
                    self.link(own, en, "reads_env", "EXTRACTED", rel, ln)
                for m in self.TOPIC.finditer(s):
                    t = m.group(1) or m.group(2)
                    rel_kind = "subscribes_to" if "subscri" in s else "publishes_to"
                    self.link(own, self.node("pubsub_topic", t, f"Pub/Sub: {t}", rel, ln), rel_kind, "EXTRACTED", rel, ln)
                for m in self.SUBSCRIPTION.finditer(s):
                    t = m.group(1) or m.group(2)
                    self.link(own, self.node("pubsub_sub", t, f"Pub/Sub sub: {t}", rel, ln), "subscribes_to", "EXTRACTED", rel, ln)
                for m in self.COLLECTION.finditer(s):
                    self.link(own, self.node("firestore", m.group(1), f"Firestore: {m.group(1)}", rel, ln), "uses_collection", "EXTRACTED", rel, ln)
                for m in self.BUCKET.finditer(s):
                    self.link(own, self.node("gcs_bucket", m.group(1), f"GCS: {m.group(1)}", rel, ln), "uses_bucket", "EXTRACTED", rel, ln)
                for m in self.DATASET.finditer(s):
                    self.link(own, self.node("bigquery", m.group(1), f"BigQuery: {m.group(1)}", rel, ln), "uses_dataset", "EXTRACTED", rel, ln)
                for m in self.SECRET_MGR.finditer(s):
                    self.link(own, self.node("secret", m.group(1), f"Secret Manager: {m.group(1)}", rel, ln), "reads_secret", "EXTRACTED", rel, ln)
                for m in self.HTTP_LIT.finditer(s):
                    if "." in m.group(1):  # ignora stubs internos tipo https://room/... de Durable Objects
                        self.link(own, self.host_node(m.group(1)), "calls_external", "EXTRACTED", rel, ln)
                if self.SQL_HINT.search(s):
                    for m in self.SQL.finditer(s):
                        verb = "writes_table" if m.group(1).upper() in ("INTO", "UPDATE") else "reads_table"
                        t = m.group(2)
                        self.link(own, self.node("sql_table", t, f"Tabla: {t}", rel, ln), verb, "EXTRACTED", rel, ln)
        # env de Cloud Run/Functions que vienen de Secret Manager
        for env, sec in self.gcp_secret_env.items():
            en = f"infra_env_{slug(env)}"
            if en in self.nodes:
                self.link(en, sec, "from_secret", "EXTRACTED", self.local(self.nodes[sec]["source_file"]), 1)
        # Con un único servicio de Cloud Run/App Engine, sus rutas son las del proyecto (INFERRED)
        if len(set(self.gcp_services)) == 1:
            svc = self.gcp_services[0]
            for nid, n in list(self.nodes.items()):
                if n["infra_kind"] == "route":
                    self.link(svc, nid, "serves", "INFERRED", self.local(n["source_file"]), 1)

    def route(self, verb: str, path: str, rel: str, ln: int) -> str:
        rn = self.node("route", f"{verb}_{path}_{rel}", f"{verb} {path}", rel, ln)
        if self.cf_workers:
            # cuelga la ruta del Worker cuyo main vive en el mismo directorio
            for name, info in self.cf_workers.items():
                cfg_dir = str(Path(info["config"]).parent)
                if cfg_dir in (".", "") or rel.startswith(cfg_dir + "/"):
                    self.link(f"infra_worker_{slug(name)}", rn, "serves", "EXTRACTED", rel, ln)
        return rn

    def cf_env_use(self, own: str, name: str, rel: str, ln: int):
        if name in self.cf_bindings:
            self.link(own, self.cf_bindings[name], "uses_binding", "EXTRACTED", rel, ln)
        elif name in self.cf_vars:
            self.link(own, self.cf_vars[name], "reads_var", "EXTRACTED", rel, ln)
        else:
            sn = self.node("cf_secret", name, f"secret {name}", rel, ln)
            self.link(own, sn, "reads_secret", "INFERRED", rel, ln)

    # ========================================================================
    def run(self):
        self.cloudflare()
        self.gcp()
        self.code()
        return self

    def apply(self):
        g = self.graph
        g["nodes"] = [n for n in g["nodes"] if n.get("_origin") != ORIGIN] + list(self.nodes.values())
        g["links"] = [l for l in g["links"] if l.get("_origin") != ORIGIN] + self.links
        return g

    # ========================================================================
    def report(self) -> str:
        label = {nid: n["label"] for nid, n in self.nodes.items()}
        label.update({n["id"]: n.get("label", n["id"]) for n in self.code_nodes})
        out = ["# INFRA_MAP — capa de infraestructura", "",
               f"Proyecto: `{self.root.name}` · {len(self.nodes)} nodos infra · {len(self.links)} edges infra", "",
               "Generado por `graphify-cloud` (infra_overlay.py). EXTRACTED = literal en config/código; "
               "INFERRED = deducido, revisar.", ""]
        if self.warnings:
            out += ["## ⚠️ Avisos", ""] + [f"- {w}" for w in self.warnings] + [""]
        for name, info in self.cf_workers.items():
            out += [f"## Worker `{name}`", "", f"- Config: `{info['config']}` · main: `{info['main']}`"]
            if info["routes"]:
                out.append(f"- Rutas públicas: {', '.join(info['routes'])}")
            if info["crons"]:
                out.append(f"- Crons: {', '.join(info['crons'])}")
            if info["bindings"]:
                out.append(f"- Bindings: {', '.join(info['bindings'])}")
            if info["vars"]:
                out.append(f"- Vars: {', '.join(info['vars'])}")
            out.append("")
        gcp = [n for n in self.nodes.values() if n["infra_kind"] in ("cloud_run", "cloud_function", "app_engine")]
        if gcp:
            out += ["## Servicios Google Cloud", ""]
            for n in sorted(gcp, key=lambda x: x["label"]):
                outs = [l for l in self.links if l["source"] == n["id"]]
                ins = [l for l in self.links if l["target"] == n["id"] and l["relation"] == "triggers"]
                parts = [f"`{n['source_file']}`"]
                if ins:
                    parts.append("trigger: " + ", ".join(label[l["source"]] for l in ins))
                envs = [label[l["target"]].split(" ", 1)[1] for l in outs if l["relation"] == "declares"]
                if envs:
                    parts.append("env: " + ", ".join(envs))
                ep = [label[l["target"]] for l in outs if l["relation"] == "entrypoint"]
                if ep:
                    parts.append("entry: " + ", ".join(ep))
                out.append(f"- **{n['label']}** · " + " · ".join(parts))
            out.append("")
        secrets = [n for n in self.nodes.values() if n["infra_kind"] == "cf_secret"]
        if secrets:
            out += ["## Secretos usados en código y no declarados en wrangler (INFERRED)", "",
                    "Deben existir vía `wrangler secret put` — si falta alguno, el Worker falla en runtime.", ""]
            for s in secrets:
                users = sorted({label[l["source"]] for l in self.links if l["target"] == s["id"]})
                out.append(f"- `{s['label'].split(' ', 1)[1]}` ← {', '.join(users)}")
            out.append("")
        groups = defaultdict(list)
        for l in self.links:
            groups[l["relation"]].append(l)
        order = ["serves", "handled_by", "triggers", "uses_binding", "reads_table", "writes_table", "publishes_to",
                 "subscribes_to", "uses_collection", "uses_bucket", "uses_dataset", "reads_env", "from_secret",
                 "reads_secret", "reads_var", "calls_external", "points_to", "sends_to", "consumed_by",
                 "implemented_by", "calls_worker", "entrypoint"]
        out += ["## Conexiones", ""]
        for rel in order + sorted(set(groups) - set(order)):
            if rel not in groups or rel == "declares":
                continue
            out.append(f"### {rel}")
            for l in sorted(groups[rel], key=lambda x: (label[x["source"]], label[x["target"]])):
                tag = "🟢" if l["confidence"] == "EXTRACTED" else "🟡"
                out.append(f"- {tag} {label[l['source']]} → **{label[l['target']]}**  "
                           f"_({l['source_file']}:{l['source_location']})_")
            out.append("")
        return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--graph", help="graph.json (default <path>/graphify-out/graph.json)")
    ap.add_argument("--report", help="salida markdown (default <path>/graphify-out/INFRA_MAP.md)")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    gpath = Path(args.graph) if args.graph else root / "graphify-out" / "graph.json"
    if not gpath.exists():
        sys.exit(f"No existe {gpath}. Corre primero: graphify update {args.path}")
    graph = json.loads(gpath.read_text())
    ov = Overlay(root, graph).run()
    gpath.write_text(json.dumps(ov.apply(), ensure_ascii=False))
    rpath = Path(args.report) if args.report else gpath.parent / "INFRA_MAP.md"
    rpath.write_text(ov.report(), encoding="utf-8")
    print(f"infra overlay: +{len(ov.nodes)} nodos, +{len(ov.links)} edges -> {gpath} · reporte {rpath}")
    for w in ov.warnings:
        print(f"  aviso: {w}")


if __name__ == "__main__":
    main()
