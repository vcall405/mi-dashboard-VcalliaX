#!/usr/bin/env python3
"""Valida SYSTEM.json, OPERATIONS_HISTORY.json y DASHBOARD_CONTEXT.json contra el
contrato de datos del dashboard de Vcallia (ver ../references/data-contract.md).

Uso:
    python3 validate_dashboard_json.py
    python3 validate_dashboard_json.py --system path/to/SYSTEM.json \
        --history path/to/OPERATIONS_HISTORY.json \
        --context path/to/DASHBOARD_CONTEXT.json

Sin argumentos, valida los tres archivos en la raíz del repo (dos niveles arriba de
este script). Sale con código 1 si hay al menos un error.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

VALID_HEALTH = {"healthy", "degraded", "critical"}


def load_json(path: Path, errors: list[str]) -> dict | list | None:
    if not path.exists():
        errors.append(f"{path.name}: archivo no encontrado en {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: JSON invalido ({exc})")
        return None


def require(obj: dict, key: str, kind, label: str, errors: list[str]) -> None:
    if key not in obj:
        errors.append(f"{label}: falta el campo '{key}'")
        return
    if not isinstance(obj[key], kind):
        errors.append(
            f"{label}: '{key}' deberia ser {kind.__name__}, es {type(obj[key]).__name__}"
        )


def validate_system(path: Path, errors: list[str]) -> None:
    data = load_json(path, errors)
    if data is None:
        return
    if not isinstance(data, dict):
        errors.append(f"{path.name}: la raiz deberia ser un objeto")
        return

    label = path.name
    for key, kind in [
        ("generated_at", str),
        ("last_execution", str),
        ("executions_today", (int, float)),
        ("successful_today", (int, float)),
        ("active_workflows", (int, float)),
        ("critical_alerts", (int, float)),
        ("success_rate", (int, float)),
        ("health", str),
        ("recent_executions", list),
        ("workflows", list),
        ("hourly_activity", list),
    ]:
        require(data, key, kind, label, errors)

    if isinstance(data.get("health"), str) and data["health"] not in VALID_HEALTH:
        errors.append(
            f"{label}: 'health' es '{data['health']}', esperado uno de {sorted(VALID_HEALTH)}"
        )


def validate_history(path: Path, errors: list[str]) -> None:
    data = load_json(path, errors)
    if data is None:
        return
    if not isinstance(data, dict):
        errors.append(f"{path.name}: la raiz deberia ser un objeto")
        return

    label = path.name
    require(data, "snapshots", list, label, errors)
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list):
        return
    if len(snapshots) == 0:
        errors.append(f"{label}: 'snapshots' esta vacio")
    if len(snapshots) < 7:
        print(
            f"AVISO: {label}: solo {len(snapshots)} snapshots (se recomiendan >= 7)",
            file=sys.stderr,
        )

    seen_dates = set()
    prev_date = None
    for i, snap in enumerate(snapshots):
        item_label = f"{label}.snapshots[{i}]"
        if not isinstance(snap, dict):
            errors.append(f"{item_label}: deberia ser un objeto")
            continue
        for key, kind in [
            ("date", str),
            ("executions_today", (int, float)),
            ("success_rate", (int, float)),
            ("critical_alerts", (int, float)),
        ]:
            require(snap, key, kind, item_label, errors)
        date = snap.get("date")
        if isinstance(date, str):
            if date in seen_dates:
                errors.append(f"{label}: fecha duplicada '{date}'")
            seen_dates.add(date)
            if prev_date is not None and date > prev_date:
                errors.append(
                    f"{label}: snapshots fuera de orden en indice {i} "
                    f"('{date}' deberia venir antes de '{prev_date}')"
                )
            prev_date = date


def validate_context(path: Path, errors: list[str]) -> None:
    data = load_json(path, errors)
    if data is None:
        return
    if not isinstance(data, dict):
        errors.append(f"{path.name}: la raiz deberia ser un objeto")
        return

    label = path.name
    for key in ("projects", "tasks", "notes", "changes"):
        require(data, key, list, label, errors)

    for i, proj in enumerate(data.get("projects", []) or []):
        item_label = f"{label}.projects[{i}]"
        if not isinstance(proj, dict):
            errors.append(f"{item_label}: deberia ser un objeto")
            continue
        for key, kind in [("name", str), ("progress", str), ("status", str)]:
            require(proj, key, kind, item_label, errors)

    for i, task in enumerate(data.get("tasks", []) or []):
        item_label = f"{label}.tasks[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{item_label}: deberia ser un objeto")
            continue
        require(task, "text", str, item_label, errors)
        require(task, "done", bool, item_label, errors)

    for i, note in enumerate(data.get("notes", []) or []):
        if not isinstance(note, str):
            errors.append(f"{label}.notes[{i}]: deberia ser un string")

    for i, change in enumerate(data.get("changes", []) or []):
        item_label = f"{label}.changes[{i}]"
        if not isinstance(change, dict):
            errors.append(f"{item_label}: deberia ser un objeto")
            continue
        for key in ("title", "meta", "impact"):
            require(change, key, str, item_label, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", type=Path, default=REPO_ROOT / "SYSTEM.json")
    parser.add_argument(
        "--history", type=Path, default=REPO_ROOT / "OPERATIONS_HISTORY.json"
    )
    parser.add_argument(
        "--context", type=Path, default=REPO_ROOT / "DASHBOARD_CONTEXT.json"
    )
    args = parser.parse_args()

    errors: list[str] = []
    validate_system(args.system, errors)
    validate_history(args.history, errors)
    validate_context(args.context, errors)

    if errors:
        print(f"{len(errors)} problema(s) encontrado(s):\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("OK: los tres archivos cumplen el contrato de datos del dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
