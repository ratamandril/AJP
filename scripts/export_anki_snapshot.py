#!/usr/bin/env python3
"""Export a structured, read-only inventory of an Anki collection via AnkiConnect.

The snapshot is descriptive JSON, not a collection backup and not an importable content pack.
Only Python's standard library is required. Anki must be open with AnkiConnect enabled.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

API_VERSION = 6
PROTOCOL_VERSION = "0.1.0"  # Content-pack protocol and snapshot schema are versioned independently.
SNAPSHOT_SCHEMA_VERSION = "0.2.0"
EXPORTER_VERSION = "0.2.0"
DEFAULT_URL = "http://127.0.0.1:8765"
BATCH_SIZE = 200
SCHEDULING_KEYS = {
    "type", "queue", "due", "interval", "factor", "reps", "lapses", "left",
    "flags", "data", "original_due", "originalDue", "originalDeck", "original_deck",
    "odue", "odid", "ivl", "ease", "reps", "lapses",
}


def chunks(values: list[Any], size: int = BATCH_SIZE) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


class AnkiConnect:
    def __init__(self, url: str, timeout: int = 90):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> Any:
        body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                f"No se pudo contactar con AnkiConnect en {self.url}: {exc}. "
                "Comprueba que Anki esté abierto y AnkiConnect instalado."
            ) from exc
        if not isinstance(payload, dict) or "result" not in payload or "error" not in payload:
            raise RuntimeError(f"Respuesta no válida de AnkiConnect para la acción {action!r}.")
        if payload.get("error") is not None:
            raise RuntimeError(f"AnkiConnect action={action}: {payload['error']}")
        return payload.get("result")


def safe_optional_call(client: AnkiConnect, action: str, capabilities: dict[str, Any], **params: Any) -> Any:
    """Call an optional API action and record availability without failing the whole export."""
    try:
        result = client.call(action, **params)
        capabilities[action] = {"available": True}
        return result
    except Exception as exc:  # Optional metadata must not prevent a useful snapshot.
        capabilities[action] = {"available": False, "error": str(exc)}
        return None


def normalized_model(model: dict[str, Any], fields_on_templates: Any = None) -> dict[str, Any]:
    """Expose an analysis-friendly model view while retaining the full Anki definition."""
    raw_fields = model.get("flds", []) or []
    raw_templates = model.get("tmpls", []) or []
    fields: list[dict[str, Any]] = []
    for field in raw_fields:
        fields.append({
            "name": field.get("name"),
            "order": field.get("ord"),
            "font": field.get("font"),
            "size": field.get("size"),
            "sticky": field.get("sticky"),
            "rtl": field.get("rtl"),
            "description": field.get("description", field.get("desc", "")),
            "plain_text": field.get("plainText"),
            "collapsed": field.get("collapsed"),
            "exclude_from_search": field.get("excludeFromSearch"),
            "anki_field_id": field.get("id"),
            "tag": field.get("tag"),
            "prevent_deletion": field.get("preventDeletion"),
        })
    templates: list[dict[str, Any]] = []
    for template in raw_templates:
        templates.append({
            "name": template.get("name"),
            "order": template.get("ord"),
            "question_html": template.get("qfmt", ""),
            "answer_html": template.get("afmt", ""),
            "browser_question_html": template.get("bqfmt", ""),
            "browser_answer_html": template.get("bafmt", ""),
            "deck_override_id": template.get("did"),
            "browser_font": template.get("bfont"),
            "browser_font_size": template.get("bsize"),
            "anki_template_id": template.get("id"),
        })
    return {
        "name": model.get("name"),
        "anki_id": model.get("id"),
        "kind": "cloze" if model.get("type") == 1 else "standard",
        "anki_kind_code": model.get("type"),
        "sort_field_index": model.get("sortf"),
        "default_deck_id": model.get("did"),
        "fields": fields,
        "card_templates": templates,
        "fields_on_templates": fields_on_templates,
        "css": model.get("css", ""),
        "requirements": model.get("req", []),
        "latex_pre": model.get("latexPre", ""),
        "latex_post": model.get("latexPost", ""),
        "modified_unix": model.get("mod"),
        # The complete object preserves less-common properties not normalized above.
        "raw_definition": model,
    }


def media_references(text: str) -> set[str]:
    found: set[str] = set()
    for match in re.findall(r"<img\b[^>]*\bsrc\s*=\s*['\"]?([^\s'\">]+)", text, flags=re.I):
        found.add(match)
    for match in re.findall(r"\[sound:([^\]]+)\]", text, flags=re.I):
        found.add(match)
    for match in re.findall(r"url\(\s*['\"]?([^)'\"]+)", text, flags=re.I):
        value = match.strip()
        if value and not value.startswith(("http://", "https://", "data:", "//", "#")):
            found.add(value)
    return found


def deck_record(name: str, deck_id: Any) -> dict[str, Any]:
    parts = name.split("::")
    return {
        "name": name,
        "anki_id": deck_id,
        "path_segments": parts,
        "parent_name": "::".join(parts[:-1]) if len(parts) > 1 else None,
        "leaf_name": parts[-1],
        "is_filtered": None,  # Filled when deck configuration is requested and available.
    }


def plain_fields(fields_detail: dict[str, Any]) -> dict[str, Any]:
    return {
        name: (value.get("value", "") if isinstance(value, dict) else value)
        for name, value in fields_detail.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"AnkiConnect URL (predeterminado: {DEFAULT_URL})")
    parser.add_argument("--out", default="anki_snapshot.json", help="Ruta del archivo JSON de salida")
    parser.add_argument("--no-notes", action="store_true", help="No guardar los campos ni el contenido de las notas")
    parser.add_argument("--no-cards", action="store_true", help="No guardar tarjetas renderizadas")
    parser.add_argument("--include-scheduling", action="store_true", help="Incluir metadatos de programación de tarjetas")
    parser.add_argument("--include-deck-configs", action="store_true", help="Incluir configuraciones de repaso de los mazos")
    parser.add_argument("--include-media-inventory", action="store_true", help="Incluir el listado de nombres de archivos multimedia de Anki")
    parser.add_argument("--no-tag-inventory", action="store_true", help="No incluir la lista global de etiquetas")
    parser.add_argument("--schema-only", action="store_true", help="Exportar definiciones y conteos, pero no los campos de notas ni tarjetas renderizadas")
    parser.add_argument("--no-template-field-usage", action="store_true", help="No consultar el uso de campos por plantilla (acelera colecciones con muchos tipos)")
    args = parser.parse_args()

    if args.schema_only:
        args.no_notes = True
        args.no_cards = True

    client = AnkiConnect(args.url)
    capabilities: dict[str, Any] = {}
    warnings: list[str] = []
    print("Conectando con AnkiConnect…", file=sys.stderr)
    api_version = client.call("version")

    deck_map = client.call("deckNamesAndIds") or {}
    deck_list = [deck_record(name, deck_id) for name, deck_id in sorted(deck_map.items(), key=lambda kv: kv[0].casefold())]
    failed_deck_configs: list[str] = []
    successful_deck_configs = 0
    if args.include_deck_configs:
        for deck in deck_list:
            config = safe_optional_call(client, "getDeckConfig", capabilities, deck=deck["name"])
            if config is not None:
                deck["config"] = config
                deck["is_filtered"] = bool(config.get("dyn", False))
                successful_deck_configs += 1
            else:
                failed_deck_configs.append(deck["name"])
                warnings.append(f"No se pudo exportar la configuración del mazo {deck['name']!r}.")
        capabilities["getDeckConfig"] = {
            "available": successful_deck_configs > 0,
            "successful_deck_count": successful_deck_configs,
            "failed_deck_names": failed_deck_configs,
            "requested": True,
        }
    else:
        capabilities["getDeckConfig"] = {"available": None, "requested": False}

    model_map = client.call("modelNamesAndIds") or {}
    model_ids = list(model_map.values())
    model_id_by_name = {name: model_id for name, model_id in model_map.items()}
    deck_id_by_name = dict(deck_map)
    raw_models: list[dict[str, Any]] = []
    for group in chunks(model_ids):
        if group:
            raw_models.extend(client.call("findModelsById", modelIds=group) or [])
    raw_models.sort(key=lambda item: str(item.get("name", "")).casefold())

    fields_on_templates_by_model: dict[str, Any] = {}
    failed_template_usage: list[str] = []
    if not args.no_template_field_usage:
        for model in raw_models:
            model_name = model.get("name")
            usage = safe_optional_call(client, "modelFieldsOnTemplates", capabilities, modelName=model_name)
            if usage is not None:
                fields_on_templates_by_model[str(model_name)] = usage
            else:
                failed_template_usage.append(str(model_name))
                warnings.append(f"No se pudo calcular fields_on_templates para {model_name!r}; la definición HTML sí está incluida.")
        capabilities["modelFieldsOnTemplates"] = {
            "available": bool(fields_on_templates_by_model),
            "successful_model_count": len(fields_on_templates_by_model),
            "failed_model_names": failed_template_usage,
            "requested": True,
        }
    else:
        capabilities["modelFieldsOnTemplates"] = {"available": None, "requested": False}

    models = [normalized_model(item, fields_on_templates_by_model.get(str(item.get("name")))) for item in raw_models]

    # IDs are queried in every mode so schema-only snapshots still describe collection scale
    # without writing note/card contents or internal IDs into the JSON.
    print("Contando notas y tarjetas…", file=sys.stderr)
    note_ids = client.call("findNotes", query="") or []
    card_ids = client.call("findCards", query="") or []

    notes: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    note_type_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    if not args.no_notes:
        print("Leyendo notas…", file=sys.stderr)
        for group in chunks(note_ids):
            if not group:
                continue
            for note in client.call("notesInfo", notes=group) or []:
                fields_detail = note.get("fields", {}) or {}
                fields = plain_fields(fields_detail)
                model_name = note.get("modelName", "")
                note_type_counts[model_name] += 1
                tag_counts.update(note.get("tags", []))
                notes.append({
                    "note_id": note.get("noteId"),
                    "note_type": model_name,
                    "note_type_anki_id": model_id_by_name.get(model_name),
                    "tags": note.get("tags", []),
                    "fields": fields,
                    "fields_detail": fields_detail,
                    "modified_unix": note.get("mod"),
                    "profile": note.get("profile"),
                    "card_ids": note.get("cards", []),
                })

    if not args.no_cards:
        print("Leyendo tarjetas generadas/renderizadas…", file=sys.stderr)
        for group in chunks(card_ids):
            if not group:
                continue
            for card in client.call("cardsInfo", cards=group) or []:
                record = {
                    "card_id": card.get("cardId"),
                    "note_id": card.get("note"),
                    "note_type": card.get("modelName"),
                    "note_type_anki_id": model_id_by_name.get(card.get("modelName")),
                    "deck": card.get("deckName"),
                    "deck_anki_id": deck_id_by_name.get(card.get("deckName")),
                    "template_order": card.get("ord"),
                    "field_order": card.get("fieldOrder"),
                    "question_html": card.get("question", ""),
                    "answer_html": card.get("answer", ""),
                    "modified_unix": card.get("mod"),
                }
                if args.include_scheduling:
                    for key in SCHEDULING_KEYS:
                        if key in card:
                            record[key] = card[key]
                cards.append(record)

    # Connect rendered card records to template names where both are available.
    template_lookup = {
        (model.get("name"), template.get("order")): template.get("name")
        for model in models for template in model.get("card_templates", [])
    }
    for card in cards:
        card["template_name"] = template_lookup.get((card.get("note_type"), card.get("template_order")))

    cards_by_deck = Counter(card.get("deck", "") for card in cards)
    cards_by_type = Counter(card.get("note_type", "") for card in cards)
    all_tags = None
    if not args.no_tag_inventory:
        result = safe_optional_call(client, "getTags", capabilities)
        if result is not None:
            all_tags = sorted(result, key=str.casefold)
        else:
            warnings.append("No se pudo exportar el vocabulario global de etiquetas; las etiquetas usadas en notas completas sí aparecen.")

    media: set[str] = set()
    for model in raw_models:
        media.update(media_references(str(model.get("css", ""))))
        media.update(media_references(str(model.get("latexPre", ""))))
        media.update(media_references(str(model.get("latexPost", ""))))
        for template in model.get("tmpls", []) or []:
            media.update(media_references(str(template.get("qfmt", ""))))
            media.update(media_references(str(template.get("afmt", ""))))
            media.update(media_references(str(template.get("bqfmt", ""))))
            media.update(media_references(str(template.get("bafmt", ""))))
    for note in notes:
        for value in note.get("fields", {}).values():
            if isinstance(value, str):
                media.update(media_references(value))
    for card in cards:
        media.update(media_references(str(card.get("question_html", ""))))
        media.update(media_references(str(card.get("answer_html", ""))))

    media_inventory = None
    if args.include_media_inventory:
        result = safe_optional_call(client, "getMediaFilesNames", capabilities)
        if result is not None:
            media_inventory = sorted(result, key=str.casefold)
        else:
            warnings.append("AnkiConnect no devolvió el inventario multimedia; sí se conservaron las referencias detectadas en campos y plantillas.")

    collection_summary: dict[str, Any] = {
        "deck_count": len(deck_list),
        "note_type_count": len(models),
        "note_count_in_collection": len(note_ids),
        "card_count_in_collection": len(card_ids),
        "note_count_exported": len(notes),
        "card_count_exported": len(cards),
        "notes_by_type": dict(sorted(note_type_counts.items())) if not args.no_notes else None,
        "cards_by_type": dict(sorted(cards_by_type.items())) if not args.no_cards else None,
        "cards_by_deck": dict(sorted(cards_by_deck.items())) if not args.no_cards else None,
        "tag_counts_in_exported_notes": dict(sorted(tag_counts.items())) if not args.no_notes else None,
        "global_tag_count": len(all_tags) if all_tags is not None else None,
    }

    section_status = {
        "decks": {"included": True, "records_exported": len(deck_list), "count_in_collection": len(deck_list)},
        "note_types": {"included": True, "records_exported": len(models), "count_in_collection": len(models)},
        "notes": {"included": not args.no_notes, "records_exported": len(notes), "count_in_collection": len(note_ids)},
        "cards": {"included": not args.no_cards, "records_exported": len(cards), "count_in_collection": len(card_ids)},
        "tags": {"included": all_tags is not None, "records_exported": len(all_tags) if all_tags is not None else 0},
        "media_references": {"included": True, "records_exported": len(media)},
        "media_inventory": {"included": media_inventory is not None, "records_exported": len(media_inventory) if media_inventory is not None else 0},
        "deck_configs": {"included": bool(args.include_deck_configs) and all("config" in item for item in deck_list), "records_exported": sum("config" in item for item in deck_list)},
    }

    api_actions_used = ["version", "deckNamesAndIds", "modelNamesAndIds", "findModelsById", "findNotes", "findCards"]
    if not args.no_notes:
        api_actions_used.append("notesInfo")
    if not args.no_cards:
        api_actions_used.append("cardsInfo")
    if not args.no_template_field_usage and raw_models:
        api_actions_used.append("modelFieldsOnTemplates")
    if not args.no_tag_inventory:
        api_actions_used.append("getTags")
    if args.include_deck_configs:
        api_actions_used.append("getDeckConfig")
    if args.include_media_inventory:
        api_actions_used.append("getMediaFilesNames")

    # These core actions have succeeded by the time the snapshot is constructed.
    for required_action in ["version", "deckNamesAndIds", "modelNamesAndIds", "findModelsById", "findNotes", "findCards"]:
        capabilities.setdefault(required_action, {"available": True, "required": True})

    snapshot = {
        "protocol": "anki-json-protocol",
        "protocol_version": PROTOCOL_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_kind": "collection_inventory",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exporter": {"name": "export_anki_snapshot.py", "version": EXPORTER_VERSION},
        "source": {
            "anki_connect_url": args.url,
            "anki_connect_api_version_reported": api_version,
            "anki_connect_request_version": API_VERSION,
            "api_actions_used": api_actions_used,
            "scheduling_included": bool(args.include_scheduling and not args.no_cards),
            "note_content_included": not args.no_notes,
            "rendered_cards_included": not args.no_cards,
            "deck_configs_included": bool(args.include_deck_configs),
            "tag_inventory_requested": not args.no_tag_inventory,
            "media_inventory_requested": bool(args.include_media_inventory),
            "local_collection_content_written_to_snapshot": bool(not args.no_notes or not args.no_cards),
        },
        "capabilities": capabilities,
        "sections": section_status,
        "summary": collection_summary,
        "decks": deck_list,
        "note_types": models,
        "notes": notes,
        "cards": cards,
        "tags": all_tags,
        "media_references": sorted(media, key=str.casefold),
        "media_inventory": media_inventory,
        "warnings": warnings,
        "limitations": [
            "Este snapshot es un inventario descriptivo; no es una copia de seguridad restaurable de la colección.",
            "Los IDs internos de Anki son locales a la colección y no deben utilizarse como identidades portables en content packs.",
            "Las referencias multimedia se enumeran, pero los bytes de archivos no se empaquetan en este JSON.",
            "El historial completo de repasos no se exporta; --include-scheduling solo incluye metadatos disponibles en cardsInfo.",
            "Las definiciones raw_definition conservan propiedades que el exportador no normaliza explícitamente; el formato interno de Anki puede cambiar.",
        ],
    }

    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    size = output.stat().st_size
    print(f"Exportación terminada: {output} ({size:,} bytes)", file=sys.stderr)
    print(json.dumps(collection_summary, ensure_ascii=False, indent=2), file=sys.stderr)
    if warnings:
        print(f"Advertencias: {len(warnings)}. Consulta el campo 'warnings' del JSON.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
