#!/usr/bin/env python3
"""Core validation helpers for Anki JSON Protocol content packs v0.2.0."""
from __future__ import annotations

import re
from typing import Any

PROTOCOL_NAME = "anki-json-protocol"
CONTENT_PACK_VERSION = "0.2.0"
SAFE_TAG_PREFIX = "ajp_uid_"

FIELD_TOKEN_RE = re.compile(r"{{\s*([#^/]?)\s*([^{}]+?)\s*}}")
CLOZE_RE = re.compile(r"{{\s*c\d+::.+?}}", re.DOTALL)
BUILTIN_FIELDS = {
    "FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag",
    "Note", "Template", "Suspend", "Flag", "Tags:html", "Deck:html",
}


def source_tag(source_id: str) -> str:
    """Return the deterministic, Anki-search-safe marker for a portable source_id."""
    import hashlib
    return SAFE_TAG_PREFIX + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _refs_in_template(template: str, fields: set[str]) -> list[str]:
    """Return template field references that cannot be resolved to declared fields/builtins."""
    errors: list[str] = []
    for match in FIELD_TOKEN_RE.finditer(template):
        token = match.group(2).strip()
        if not token:
            continue
        # Anki special fields that do not correspond to note fields.
        if token in BUILTIN_FIELDS or re.fullmatch(r"c\d+", token):
            continue
        # `{{=Field}}` is used by some template syntaxes for field references.
        if token.startswith("="):
            token = token[1:].strip()
        if token in fields:
            continue
        # Filters such as cloze:Text, type:Back, hint:Answer, and `tts ...:Field`.
        candidate = token.rsplit(":", 1)[-1].strip()
        if candidate in fields:
            continue
        # `tts en_US voices=...:Field` uses a colon before the field.
        if token.startswith("tts ") and candidate in fields:
            continue
        errors.append(match.group(2).strip())
    return errors


def validate_pack(pack: Any) -> list[str]:
    """Validate schema-like and cross-reference rules without network calls.

    The official JSON Schema is included separately. This function deliberately uses
    only the Python standard library so the generator remains easy to run on Windows.
    """
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["La raíz del paquete debe ser un objeto JSON."]

    allowed_root = {"$schema", "protocol", "protocol_version", "pack_id", "metadata", "decks", "note_types", "notes"}
    extra_root = set(pack) - allowed_root
    if extra_root:
        errors.append(f"Propiedades no reconocidas en la raíz: {sorted(extra_root)}.")
    if pack.get("protocol") != PROTOCOL_NAME:
        errors.append("protocol debe ser 'anki-json-protocol'.")
    if pack.get("protocol_version") != CONTENT_PACK_VERSION:
        errors.append(f"protocol_version debe ser '{CONTENT_PACK_VERSION}'.")
    if not _nonempty_string(pack.get("pack_id")) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", str(pack.get("pack_id", ""))):
        errors.append("pack_id debe ser una cadena de 1 a 200 caracteres con letras, números, punto, guion, dos puntos, barra o guion bajo.")

    metadata = pack.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata debe ser un objeto.")
    else:
        extra_metadata = set(metadata) - {"title", "language", "description", "author", "license", "created_at", "source"}
        if extra_metadata:
            errors.append(f"Propiedades no reconocidas en metadata: {sorted(extra_metadata)}.")
        for key in ("title", "language", "description"):
            if not isinstance(metadata.get(key), str) or (key != "description" and not metadata.get(key).strip()):
                errors.append(f"metadata.{key} debe ser una cadena válida.")
        if isinstance(metadata.get("language"), str) and len(metadata["language"]) < 2:
            errors.append("metadata.language debe tener al menos dos caracteres (por ejemplo, 'es').")

    decks = pack.get("decks")
    if not isinstance(decks, list):
        errors.append("decks debe ser una lista.")
        decks = []
    deck_names: set[str] = set()
    for i, deck in enumerate(decks):
        if not isinstance(deck, dict) or not _nonempty_string(deck.get("name")):
            errors.append(f"decks[{i}] debe contener un name no vacío.")
            continue
        extra = set(deck) - {"name"}
        if extra:
            errors.append(f"decks[{i}] contiene propiedades no reconocidas: {sorted(extra)}.")
        name = deck["name"].strip()
        if name in deck_names:
            errors.append(f"Mazo duplicado en el paquete: {name!r}.")
        deck_names.add(name)

    types = pack.get("note_types")
    if not isinstance(types, list):
        errors.append("note_types debe ser una lista.")
        types = []
    type_by_id: dict[str, dict[str, Any]] = {}
    type_names: set[str] = set()
    for i, model in enumerate(types):
        prefix = f"note_types[{i}]"
        if not isinstance(model, dict):
            errors.append(f"{prefix} debe ser un objeto.")
            continue
        extra_model = set(model) - {"type_id", "version", "name", "mode", "kind", "fields", "required_fields", "card_templates", "css"}
        if extra_model:
            errors.append(f"{prefix} contiene propiedades no reconocidas: {sorted(extra_model)}.")
        type_id, name, mode, kind, version = (model.get(k) for k in ("type_id", "name", "mode", "kind", "version"))
        if not _nonempty_string(type_id) or len(str(type_id)) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", str(type_id)):
            errors.append(f"{prefix}.type_id debe tener 1-200 caracteres ASCII válidos para un identificador AJP.")
        elif type_id in type_by_id:
            errors.append(f"type_id duplicado: {type_id!r}.")
        else:
            type_by_id[type_id] = model
        if not _nonempty_string(name):
            errors.append(f"{prefix}.name debe ser una cadena no vacía.")
        elif name in type_names:
            errors.append(f"Nombre de tipo de nota duplicado en el paquete: {name!r}.")
        else:
            type_names.add(name)
        if mode not in ("ensure", "reuse"):
            errors.append(f"{prefix}.mode debe ser 'ensure' o 'reuse'.")
        if kind not in ("standard", "cloze"):
            errors.append(f"{prefix}.kind debe ser 'standard' o 'cloze'.")
        if not _nonempty_string(version) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", str(version)):
            errors.append(f"{prefix}.version debe seguir una versión tipo 1.0.0.")
        if not isinstance(model.get("css"), str):
            errors.append(f"{prefix}.css debe ser una cadena (aunque esté vacía).")

        fields = model.get("fields")
        if not isinstance(fields, list) or not fields or not all(_nonempty_string(x) for x in fields):
            errors.append(f"{prefix}.fields debe ser una lista no vacía de nombres de campo.")
            fields = []
        if len(fields) != len(set(fields)):
            errors.append(f"{prefix}.fields contiene nombres repetidos.")
        field_set = set(fields)

        required_fields = model.get("required_fields")
        if not isinstance(required_fields, list) or not all(_nonempty_string(x) for x in required_fields):
            errors.append(f"{prefix}.required_fields debe ser una lista de nombres de campo.")
            required_fields = []
        if len(required_fields) != len(set(required_fields)):
            errors.append(f"{prefix}.required_fields contiene nombres repetidos.")
        unknown_required = set(required_fields) - field_set
        if unknown_required:
            errors.append(f"{prefix}.required_fields referencia campos no declarados: {sorted(unknown_required)}.")

        templates = model.get("card_templates")
        if not isinstance(templates, list) or not templates:
            errors.append(f"{prefix}.card_templates debe contener al menos una plantilla.")
            templates = []
        if kind == "cloze" and len(templates) != 1:
            errors.append(f"{prefix}: para kind='cloze' se requiere exactamente una plantilla en v0.2.0.")
        template_ids: set[str] = set()
        template_names: set[str] = set()
        cloze_filter_seen = False
        for j, template in enumerate(templates):
            tprefix = f"{prefix}.card_templates[{j}]"
            if not isinstance(template, dict):
                errors.append(f"{tprefix} debe ser un objeto.")
                continue
            extra_template = set(template) - {"template_id", "name", "question_html", "answer_html"}
            if extra_template:
                errors.append(f"{tprefix} contiene propiedades no reconocidas: {sorted(extra_template)}.")
            tid, tname = template.get("template_id"), template.get("name")
            if not _nonempty_string(tid) or len(str(tid)) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", str(tid)):
                errors.append(f"{tprefix}.template_id debe tener 1-200 caracteres ASCII válidos para un identificador AJP.")
            elif tid in template_ids:
                errors.append(f"{prefix}: template_id duplicado {tid!r}.")
            else:
                template_ids.add(tid)
            if not _nonempty_string(tname):
                errors.append(f"{tprefix}.name debe ser una cadena no vacía.")
            elif tname in template_names:
                errors.append(f"{prefix}: nombre de plantilla duplicado {tname!r}.")
            else:
                template_names.add(tname)
            for html_key in ("question_html", "answer_html"):
                html = template.get(html_key)
                if not isinstance(html, str):
                    errors.append(f"{tprefix}.{html_key} debe ser una cadena.")
                    continue
                missing = _refs_in_template(html, field_set)
                if missing:
                    errors.append(f"{tprefix}.{html_key} contiene referencias a campos desconocidos: {sorted(set(missing))}.")
                if kind == "cloze" and re.search(r"{{\s*cloze\s*:", html):
                    cloze_filter_seen = True
        if kind == "cloze" and templates and not cloze_filter_seen:
            errors.append(f"{prefix}: las plantillas cloze deben usar un filtro de eliminación cloze sobre un campo.")

    notes = pack.get("notes")
    if not isinstance(notes, list):
        errors.append("notes debe ser una lista.")
        notes = []
    source_ids: set[str] = set()
    first_fields_seen: set[tuple[str, str]] = set()
    declared_deck_names = deck_names
    for i, note in enumerate(notes):
        prefix = f"notes[{i}]"
        if not isinstance(note, dict):
            errors.append(f"{prefix} debe ser un objeto.")
            continue
        extra_note = set(note) - {"source_id", "deck", "note_type_id", "tags", "fields"}
        if extra_note:
            errors.append(f"{prefix} contiene propiedades no reconocidas: {sorted(extra_note)}.")
        source_id = note.get("source_id")
        if not _nonempty_string(source_id) or len(str(source_id)) > 500:
            errors.append(f"{prefix}.source_id debe ser una cadena de 1 a 500 caracteres.")
        elif source_id in source_ids:
            errors.append(f"source_id duplicado dentro del paquete: {source_id!r}.")
        else:
            source_ids.add(source_id)
        deck = note.get("deck")
        if not _nonempty_string(deck):
            errors.append(f"{prefix}.deck debe ser una cadena no vacía.")
        elif deck not in declared_deck_names:
            errors.append(f"{prefix}.deck {deck!r} no aparece en decks.")
        type_id = note.get("note_type_id")
        if not _nonempty_string(type_id) or len(str(type_id)) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", str(type_id)):
            errors.append(f"{prefix}.note_type_id debe ser un identificador AJP válido.")
        model = type_by_id.get(type_id) if isinstance(type_id, str) else None
        if model is None:
            errors.append(f"{prefix}.note_type_id {type_id!r} no referencia un tipo de nota declarado.")
        field_values = note.get("fields")
        if not isinstance(field_values, dict) or not field_values:
            errors.append(f"{prefix}.fields debe ser un objeto no vacío con valores de texto.")
            continue
        non_string_values = [key for key, value in field_values.items() if not isinstance(value, str)]
        if non_string_values:
            errors.append(f"{prefix}.fields solo admite cadenas; valores no válidos en {sorted(non_string_values)}.")
        if model:
            allowed_fields = set(model.get("fields", []))
            extra = set(field_values) - allowed_fields
            if extra:
                errors.append(f"{prefix} contiene campos que no pertenecen a {model.get('name')!r}: {sorted(extra)}.")
            missing_required = [f for f in model.get("required_fields", []) if not str(field_values.get(f, "")).strip()]
            if missing_required:
                errors.append(f"{prefix} no completa los campos obligatorios: {missing_required}.")
            first_field = model.get("fields", [None])[0]
            first_value = field_values.get(first_field, "") if first_field else ""
            if not isinstance(first_value, str) or not first_value.strip():
                errors.append(f"{prefix} debe tener contenido en el primer campo de Anki ({first_field!r}).")
            else:
                dedupe_key = (str(type_id), first_value.strip().casefold())
                if dedupe_key in first_fields_seen:
                    errors.append(f"{prefix} repite el primer campo de otra nota del mismo tipo: {first_value!r}. El paquete v0.2.0 no habilita duplicados intencionales.")
                first_fields_seen.add(dedupe_key)
            if model.get("kind") == "cloze":
                cloze_fields = set()
                for template in model.get("card_templates", []):
                    for html_key in ("question_html", "answer_html"):
                        html = template.get(html_key, "")
                        for token in re.findall(r"{{\s*cloze\s*:\s*([^{}]+?)\s*}}", html):
                            candidate = token.strip().rsplit(":", 1)[-1].strip()
                            if candidate in allowed_fields:
                                cloze_fields.add(candidate)
                has_deletion = any(CLOZE_RE.search(str(field_values.get(field, ""))) for field in cloze_fields)
                if cloze_fields and not has_deletion:
                    errors.append(f"{prefix} usa un tipo Cloze, pero ninguno de los campos filtrados contiene una eliminación {{c1::...}}.")
        tags = note.get("tags")
        if not isinstance(tags, list) or not all(_nonempty_string(tag) and not any(ch.isspace() for ch in tag) for tag in tags):
            errors.append(f"{prefix}.tags debe ser una lista de etiquetas no vacías, sin espacios.")
        elif len(tags) != len(set(tags)):
            errors.append(f"{prefix}.tags contiene etiquetas repetidas.")

    return errors


def model_signature_from_spec(model: dict[str, Any]) -> dict[str, Any]:
    """Return the part of a note type definition that AJP v0.2.0 manages."""
    return {
        "name": model["name"],
        "kind": model["kind"],
        "fields": list(model["fields"]),
        "css": model["css"],
        "card_templates": [
            {
                "name": template["name"],
                "question_html": template["question_html"],
                "answer_html": template["answer_html"],
            }
            for template in model["card_templates"]
        ],
    }


def model_signature_from_anki(raw_model: dict[str, Any]) -> dict[str, Any]:
    """Normalize an AnkiConnect findModelsById result into the managed signature."""
    fields = sorted(raw_model.get("flds", []), key=lambda f: f.get("ord", 0))
    templates = sorted(raw_model.get("tmpls", []), key=lambda t: t.get("ord", 0))
    return {
        "name": raw_model.get("name"),
        "kind": "cloze" if raw_model.get("type") == 1 else "standard",
        "fields": [f.get("name") for f in fields],
        "css": raw_model.get("css", ""),
        "card_templates": [
            {
                "name": t.get("name"),
                "question_html": t.get("qfmt", ""),
                "answer_html": t.get("afmt", ""),
            }
            for t in templates
        ],
        "unsupported_deck_overrides": [t.get("name") for t in templates if t.get("did") is not None],
    }
