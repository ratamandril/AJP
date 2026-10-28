#!/usr/bin/env python3
"""Apply explicit, preconditioned field updates to AJP-managed Anki notes.

Default is read-only planning. Apply requires confirmation of the active profile.
Only notes carrying the deterministic AJP source_id marker are eligible.
No note type, card template, deck, tag, or scheduling data is changed by this tool.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ajp_core import source_tag

API_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"
PROTOCOL = "anki-json-note-update-pack"
PROTOCOL_VERSION = "0.1.0"


class AnkiConnect:
    def __init__(self, url: str = DEFAULT_URL, timeout: int = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> Any:
        body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo contactar con AnkiConnect en {self.url}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            reason = payload.get("error") if isinstance(payload, dict) else "respuesta JSON inesperada"
            raise RuntimeError(f"AnkiConnect action={action}: {reason}")
        return payload.get("result")


def load_pack(path: Path) -> dict[str, Any]:
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el paquete {path}: {exc}") from exc
    errors = validate_update_pack(pack)
    if errors:
        raise RuntimeError("Paquete de actualización no válido:\n" + "\n".join(f"  - {e}" for e in errors))
    return pack


def validate_update_pack(pack: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["La raíz debe ser un objeto JSON."]
    allowed = {"$schema", "protocol", "protocol_version", "pack_id", "metadata", "updates"}
    extra = set(pack) - allowed
    if extra:
        errors.append(f"Propiedades no reconocidas en la raíz: {sorted(extra)}")
    if pack.get("protocol") != PROTOCOL:
        errors.append(f"protocol debe ser {PROTOCOL!r}.")
    if pack.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"protocol_version debe ser {PROTOCOL_VERSION!r}.")
    pack_id = pack.get("pack_id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        errors.append("pack_id debe ser una cadena no vacía.")
    metadata = pack.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata debe ser un objeto.")
    else:
        if set(metadata) - {"title", "description", "author"}:
            errors.append("metadata contiene propiedades no reconocidas.")
        for key in ("title", "description"):
            if not isinstance(metadata.get(key), str) or (key == "title" and not metadata[key].strip()):
                errors.append(f"metadata.{key} debe ser una cadena válida.")
    updates = pack.get("updates")
    if not isinstance(updates, list) or not updates:
        errors.append("updates debe ser una lista no vacía.")
        updates = []
    seen: set[str] = set()
    for i, item in enumerate(updates):
        p = f"updates[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{p} debe ser un objeto.")
            continue
        extra_item = set(item) - {"source_id", "expected_model_name", "expected_fields", "set_fields", "reason"}
        if extra_item:
            errors.append(f"{p} contiene propiedades no reconocidas: {sorted(extra_item)}")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"{p}.source_id debe ser una cadena no vacía.")
        elif source_id in seen:
            errors.append(f"source_id duplicado en updates: {source_id!r}.")
        else:
            seen.add(source_id)
        if not isinstance(item.get("expected_model_name"), str) or not item["expected_model_name"].strip():
            errors.append(f"{p}.expected_model_name debe ser una cadena no vacía.")
        expected = item.get("expected_fields")
        changes = item.get("set_fields")
        for key, obj in (("expected_fields", expected), ("set_fields", changes)):
            if not isinstance(obj, dict) or not obj:
                errors.append(f"{p}.{key} debe ser un objeto no vacío de valores de texto.")
            elif any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in obj.items()):
                errors.append(f"{p}.{key} debe mapear nombres de campo a valores de texto.")
        if isinstance(expected, dict) and isinstance(changes, dict):
            missing_guards = set(changes) - set(expected)
            if missing_guards:
                errors.append(f"{p}.expected_fields debe incluir precondiciones para todos los campos a cambiar: {sorted(missing_guards)}.")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            errors.append(f"{p}.reason debe ser texto si se proporciona.")
    return errors


def extract_fields(note_info: dict[str, Any]) -> dict[str, str]:
    raw = note_info.get("fields") or {}
    return {
        name: str(value.get("value", "")) if isinstance(value, dict) else str(value)
        for name, value in raw.items()
    }


def inspect_update(client: AnkiConnect, update: dict[str, Any]) -> dict[str, Any]:
    source_id = update["source_id"]
    tag = source_tag(source_id)
    note_ids = client.call("findNotes", query=f"tag:{tag}") or []
    base: dict[str, Any] = {"source_id": source_id, "status": "conflict", "changes": {}, "errors": []}
    if len(note_ids) != 1:
        base["errors"].append(
            f"Se esperaba exactamente una nota marcada para source_id; se encontraron {len(note_ids)}."
        )
        return base
    infos = client.call("notesInfo", notes=note_ids) or []
    if len(infos) != 1 or not isinstance(infos[0], dict):
        base["errors"].append("AnkiConnect no devolvió información completa de la nota.")
        return base
    info = infos[0]
    note_id = info.get("noteId")
    base["note_id"] = note_id
    tags = set(info.get("tags") or [])
    if tag not in tags:
        base["errors"].append("La nota encontrada no contiene el marcador de propiedad esperado.")
    actual_model = info.get("modelName")
    base["actual_model_name"] = actual_model
    if actual_model != update["expected_model_name"]:
        base["errors"].append(
            f"Tipo de nota inesperado: {actual_model!r}; se esperaba {update['expected_model_name']!r}."
        )
    current_fields = extract_fields(info)
    expected_fields = update["expected_fields"]
    set_fields = update["set_fields"]
    for field in sorted(set(expected_fields) | set(set_fields)):
        if field not in current_fields:
            base["errors"].append(f"El campo {field!r} no existe en la nota actual.")
    if base["errors"]:
        return base

    changes: dict[str, str] = {}
    already_desired: list[str] = []
    for field, expected_value in expected_fields.items():
        current = current_fields[field]
        if field not in set_fields:
            if current != expected_value:
                base["errors"].append(
                    f"Precondición obsoleta para {field!r}: el valor actual no coincide con expected_fields."
                )
            continue
        desired = set_fields[field]
        if current == desired:
            already_desired.append(field)
        elif current == expected_value:
            changes[field] = desired
        else:
            base["errors"].append(
                f"Conflicto para {field!r}: el valor actual no es ni el esperado ni el valor de destino."
            )
    if base["errors"]:
        return base
    base["changes"] = changes
    base["already_desired_fields"] = already_desired
    base["current_fields_subset"] = {key: current_fields[key] for key in expected_fields}
    if changes:
        base["status"] = "update"
    else:
        base["status"] = "already_applied" if already_desired else "no_op"
    return base


def write_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", help="Archivo anki-json-note-update-pack.json")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"AnkiConnect URL (predeterminado: {DEFAULT_URL})")
    parser.add_argument("--apply", action="store_true", help="Aplica cambios; sin este indicador solo planifica.")
    parser.add_argument("--confirm-profile", help="Debe coincidir exactamente con el perfil activo para autorizar --apply.")
    parser.add_argument("--report-out", help="Ruta opcional del informe JSON.")
    args = parser.parse_args()

    pack = load_pack(Path(args.pack).expanduser().resolve())
    client = AnkiConnect(args.url)
    api_version = client.call("version")
    if not isinstance(api_version, int) or api_version < API_VERSION:
        raise RuntimeError(f"Versión de AnkiConnect insuficiente o inesperada: {api_version!r}; se requiere API {API_VERSION}.")
    profile = client.call("getActiveProfile")
    if not isinstance(profile, str) or not profile:
        raise RuntimeError(f"AnkiConnect no devolvió un perfil activo válido: {profile!r}.")
    if args.apply and args.confirm_profile != profile:
        raise RuntimeError(
            f"Escritura bloqueada: perfil activo {profile!r}; para autorizarlo pasa --confirm-profile {profile!r}."
        )

    inspected = [inspect_update(client, item) for item in pack["updates"]]
    conflicts = [item for item in inspected if item["status"] == "conflict"]
    report: dict[str, Any] = {
        "protocol": PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "pack_id": pack["pack_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "plan",
        "active_profile": profile,
        "api_version": api_version,
        "writes_attempted": False,
        "preflight_conflicts": conflicts,
        "updates": inspected,
        "applied": [],
        "already_applied": [],
        "failed": [],
        "note": "AnkiConnect no ofrece una transacción para todas las actualizaciones; una falla a mitad de la ejecución puede dejar cambios parciales. Repetir el paquete es seguro para los campos con destino ya aplicado.",
    }
    print(f"Paquete: {pack['pack_id']} | perfil: {profile} | API: {api_version}")
    print("Política: actualización explícita de campos en notas marcadas por source_id; sin cambios de tipo, mazo, etiquetas ni programación.")
    for item in inspected:
        if item["status"] == "update":
            print(f"  ACTUALIZAR source_id={item['source_id']} note_id={item['note_id']} campos={sorted(item['changes'])}")
        elif item["status"] == "already_applied":
            print(f"  YA APLICADO source_id={item['source_id']} note_id={item['note_id']}")
        elif item["status"] == "no_op":
            print(f"  SIN CAMBIOS source_id={item['source_id']} note_id={item['note_id']}")
        else:
            print(f"  CONFLICTO source_id={item['source_id']}: {'; '.join(item['errors'])}")
    if conflicts:
        print("Preflight detenido: existe al menos un conflicto; no se escribió ningún campo.", file=sys.stderr)
        write_report(args.report_out, report)
        return 2
    pending = [item for item in inspected if item["status"] == "update"]
    if not args.apply:
        print(f"Modo plan: {len(pending)} nota(s) cambiarían; no se escribió nada.")
        write_report(args.report_out, report)
        return 0

    # All updates have passed preflight before any writes are attempted.
    update_by_source_id = {u["source_id"]: u for u in pack["updates"]}
    for item in pending:
        try:
            # Re-check the compare-and-set preconditions immediately before writing.
            # This narrows the race window if the user edits the note after preflight.
            latest = inspect_update(client, update_by_source_id[item["source_id"]])
            if latest["status"] == "conflict":
                item["status"] = "conflict_after_preflight"
                item["errors"].extend(latest.get("errors", []))
                report["failed"].append({
                    "source_id": item["source_id"],
                    "note_id": latest.get("note_id", item.get("note_id")),
                    "error": "La nota cambió o dejó de ser compatible después del preflight; no se escribió.",
                    "details": latest.get("errors", []),
                })
                print(f"CONFLICTO tardío source_id={item['source_id']}: no se escribe.", file=sys.stderr)
                continue
            if latest["status"] in ("already_applied", "no_op"):
                item["status"] = latest["status"]
                item["already_desired_fields"] = latest.get("already_desired_fields", [])
                continue
            # Use the freshly checked IDs/changes, not stale preflight data.
            item["note_id"] = latest["note_id"]
            item["changes"] = latest["changes"]
            report["writes_attempted"] = True
            client.call("updateNoteFields", note={"id": item["note_id"], "fields": item["changes"]})
            verify = client.call("notesInfo", notes=[item["note_id"]]) or []
            if len(verify) != 1:
                raise RuntimeError("No se pudo verificar la nota después de updateNoteFields.")
            actual_after = extract_fields(verify[0])
            wanted = item["changes"]
            mismatches = [k for k, v in wanted.items() if actual_after.get(k) != v]
            if mismatches:
                raise RuntimeError(f"La verificación posterior falló para los campos: {mismatches}.")
            item["status"] = "applied"
            report["applied"].append({"source_id": item["source_id"], "note_id": item["note_id"], "fields": wanted})
            print(f"  APLICADO source_id={item['source_id']} campos={sorted(wanted)}")
        except Exception as exc:
            entry = {"source_id": item["source_id"], "note_id": item.get("note_id"), "error": str(exc)}
            report["failed"].append(entry)
            item["status"] = "failed"
            item["errors"].append(str(exc))
            print(f"ERROR source_id={item['source_id']}: {exc}", file=sys.stderr)
    # Mark unchanged entries explicitly in the report.
    report["already_applied"] = [
        {"source_id": item["source_id"], "note_id": item.get("note_id"), "fields": item.get("already_desired_fields", [])}
        for item in inspected if item["status"] == "already_applied"
    ]
    report["result"] = "passed" if not report["failed"] else "partial_or_failed"
    write_report(args.report_out, report)
    print(f"Resultado: {report['result']} | aplicadas={len(report['applied'])} | ya aplicadas={len(report['already_applied'])} | fallos={len(report['failed'])}")
    if args.report_out:
        print(f"Informe: {Path(args.report_out).expanduser().resolve()}")
    return 0 if not report["failed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
