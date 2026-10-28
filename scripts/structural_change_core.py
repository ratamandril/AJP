#!/usr/bin/env python3
"""Validation and comparison helpers for versioned AJP structural changes."""
from __future__ import annotations
import hashlib
import re
from typing import Any

PROTOCOL = "anki-json-structural-change-plan"
PLAN_VERSION = "0.1.0"
SOURCE_TAG_PREFIX = "ajp_uid_"
FIELD_TOKEN_RE = re.compile(r"{{\s*([#^/]?)\s*([^{}]+?)\s*}}")
BUILTINS = {"FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag", "Note", "Template", "Suspend", "Flag"}
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def source_tag(source_id: str) -> str:
    return SOURCE_TAG_PREFIX + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def target_source_id(source_id: str, type_id: str, version: str) -> str:
    seed = f"{source_id}\0{type_id}@{version}".encode("utf-8")
    return "org.anki-json-protocol.migrated." + hashlib.sha256(seed).hexdigest()[:40]


def semver_core(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"Versión semántica no válida: {value!r}.")
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def fields_in_template(template: str, declared_fields: set[str]) -> list[str]:
    unknown: list[str] = []
    for match in FIELD_TOKEN_RE.finditer(template):
        token = match.group(2).strip()
        if not token or token in BUILTINS or re.fullmatch(r"c\d+", token):
            continue
        if token.startswith("="):
            token = token[1:].strip()
        candidate = token.rsplit(":", 1)[-1].strip()
        if token in declared_fields or candidate in declared_fields:
            continue
        if token.startswith("tts ") and candidate in declared_fields:
            continue
        unknown.append(token)
    return unknown


def validate_structural_plan(plan: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["La raíz del plan estructural debe ser un objeto JSON."]
    if plan.get("protocol") != PROTOCOL:
        errors.append(f"protocol debe ser {PROTOCOL!r}.")
    if plan.get("structural_change_plan_version") != PLAN_VERSION:
        errors.append(f"structural_change_plan_version debe ser {PLAN_VERSION!r}.")
    for name in ("plan_id",):
        if not isinstance(plan.get(name), str) or not plan[name].strip():
            errors.append(f"{name} debe ser una cadena no vacía.")
    metadata = plan.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("title"), str) or not metadata["title"].strip() or not isinstance(metadata.get("description"), str):
        errors.append("metadata debe contener title y description como cadenas válidas.")
    source = plan.get("source")
    target = plan.get("target_note_type")
    if not isinstance(source, dict) or not isinstance(target, dict):
        return errors + ["source y target_note_type deben ser objetos."]
    for k in ("type_id", "version", "anki_model_name"):
        if not isinstance(source.get(k), str) or not source[k].strip():
            errors.append(f"source.{k} debe ser una cadena no vacía.")
    expected = source.get("expected_definition")
    if not isinstance(expected, dict):
        errors.append("source.expected_definition es obligatorio: asegura que el modelo origen no haya cambiado inesperadamente.")
    else:
        for k in ("kind", "fields", "card_templates", "css"):
            if k not in expected:
                errors.append(f"source.expected_definition.{k} es obligatorio.")
        if expected.get("kind") not in ("standard", "cloze"):
            errors.append("source.expected_definition.kind debe ser standard o cloze.")
        if not isinstance(expected.get("fields"), list) or not expected.get("fields") or not all(isinstance(x, str) and x.strip() for x in expected.get("fields", [])):
            errors.append("source.expected_definition.fields debe ser una lista de campos.")
        elif len(expected["fields"]) != len(set(expected["fields"])):
            errors.append("source.expected_definition.fields no puede tener duplicados.")
        if not isinstance(expected.get("card_templates"), list) or not expected.get("card_templates"):
            errors.append("source.expected_definition.card_templates debe contener plantillas.")
        if not isinstance(expected.get("css"), str):
            errors.append("source.expected_definition.css debe ser una cadena.")
        if isinstance(expected.get("card_templates"), list):
            names = [t.get("name") for t in expected["card_templates"] if isinstance(t, dict)]
            if len(names) != len(expected["card_templates"]) or any(not isinstance(n, str) or not n.strip() for n in names):
                errors.append("Cada plantilla origen debe ser un objeto con name no vacío.")
            elif len(names) != len(set(names)):
                errors.append("Los nombres de plantillas origen deben ser únicos.")
            for i, template in enumerate(expected["card_templates"]):
                if not isinstance(template, dict):
                    continue
                for key in ("question_html", "answer_html"):
                    if not isinstance(template.get(key), str):
                        errors.append(f"source.expected_definition.card_templates[{i}].{key} debe ser texto.")

    for key in ("type_id", "version", "anki_model_name", "kind", "fields", "required_fields", "card_templates", "css"):
        if key not in target:
            errors.append(f"target_note_type.{key} es obligatorio.")
    for k in ("type_id", "anki_model_name"):
        if not isinstance(target.get(k), str) or not target[k].strip():
            errors.append(f"target_note_type.{k} debe ser una cadena no vacía.")
    if target.get("kind") not in ("standard", "cloze"):
        errors.append("target_note_type.kind debe ser standard o cloze.")
    fields = target.get("fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(x, str) and x.strip() for x in fields):
        errors.append("target_note_type.fields debe ser una lista de nombres no vacíos.")
        fields = []
    elif len(fields) != len(set(fields)):
        errors.append("target_note_type.fields no puede contener duplicados.")
    required = target.get("required_fields")
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        errors.append("target_note_type.required_fields debe ser una lista de cadenas.")
        required = []
    if set(required) - set(fields):
        errors.append(f"Campos requeridos no declarados en fields: {sorted(set(required)-set(fields))}.")
    if not isinstance(target.get("css"), str):
        errors.append("target_note_type.css debe ser una cadena.")
    templates = target.get("card_templates")
    if not isinstance(templates, list) or not templates:
        errors.append("target_note_type.card_templates debe contener al menos una plantilla.")
        templates = []
    tnames, tids = [], []
    for i, t in enumerate(templates):
        if not isinstance(t, dict):
            errors.append(f"Plantilla destino {i} debe ser un objeto.")
            continue
        if not isinstance(t.get("name"), str) or not t["name"].strip():
            errors.append(f"Plantilla destino {i}.name debe ser texto no vacío.")
        else:
            tnames.append(t["name"])
        if not isinstance(t.get("template_id"), str) or not t["template_id"].strip():
            errors.append(f"Plantilla destino {i}.template_id debe ser texto no vacío.")
        else:
            tids.append(t["template_id"])
        for key in ("question_html", "answer_html"):
            if not isinstance(t.get(key), str):
                errors.append(f"Plantilla destino {i}.{key} debe ser texto.")
            else:
                unknown = fields_in_template(t[key], set(fields))
                if unknown:
                    errors.append(f"Plantilla destino {t.get('name', i)!r}.{key} usa campos desconocidos: {sorted(set(unknown))}.")
    if len(tnames) != len(set(tnames)):
        errors.append("Los nombres de las plantillas destino deben ser únicos.")
    if len(tids) != len(set(tids)):
        errors.append("Los template_id destino deben ser únicos.")

    if source.get("type_id") != target.get("type_id"):
        errors.append("type_id de origen y destino debe ser el mismo para una nueva versión de la misma familia.")
    try:
        if semver_core(target.get("version", "")) <= semver_core(source.get("version", "")):
            errors.append("La versión destino debe ser mayor que la versión origen.")
    except ValueError as exc:
        errors.append(str(exc))
    for obj, label in ((source, "origen"), (target, "destino")):
        marker = f"{obj.get('type_id')}@{obj.get('version')}"
        if marker not in str(obj.get("anki_model_name", "")):
            errors.append(f"El nombre del modelo de {label} debe incluir {marker!r}.")
    if source.get("anki_model_name") == target.get("anki_model_name"):
        errors.append("El nombre del modelo destino debe ser distinto; no se admite mutación in-place en este plan.")
    if not isinstance(plan.get("target_deck"), str) or not plan["target_deck"].strip():
        errors.append("target_deck debe ser una cadena no vacía.")

    selection = plan.get("selection")
    if not isinstance(selection, dict) or selection.get("mode") != "explicit_source_ids":
        errors.append("selection.mode debe ser explicit_source_ids.")
        source_ids = []
    else:
        source_ids = selection.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids or not all(isinstance(x, str) and x.strip() for x in source_ids):
            errors.append("selection.source_ids debe ser una lista no vacía de source_id.")
            source_ids = []
        elif len(source_ids) != len(set(source_ids)):
            errors.append("selection.source_ids no puede contener duplicados.")

    mappings = plan.get("field_mapping")
    if not isinstance(mappings, list) or not mappings:
        errors.append("field_mapping debe ser una lista no vacía.")
        mappings = []
    target_fields_mapped = []
    for i, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"field_mapping[{i}] debe ser un objeto.")
            continue
        op, tf = mapping.get("operation"), mapping.get("target_field")
        if not isinstance(tf, str) or not tf.strip():
            errors.append(f"field_mapping[{i}].target_field debe ser texto no vacío.")
        else:
            target_fields_mapped.append(tf)
        if op == "copy" and not (isinstance(mapping.get("source_field"), str) and mapping["source_field"].strip()):
            errors.append(f"field_mapping[{i}] copy requiere source_field.")
        elif op == "constant" and not isinstance(mapping.get("value"), str):
            errors.append(f"field_mapping[{i}] constant requiere value de texto.")
        elif op not in ("copy", "constant", "empty"):
            errors.append(f"field_mapping[{i}].operation no está admitida.")
    if len(target_fields_mapped) != len(set(target_fields_mapped)):
        errors.append("Cada campo destino debe aparecer una sola vez en field_mapping.")
    if set(target_fields_mapped) != set(fields):
        errors.append(f"field_mapping debe cubrir exactamente todos los campos destino; faltan {sorted(set(fields)-set(target_fields_mapped))} y sobran {sorted(set(target_fields_mapped)-set(fields))}.")
    if isinstance(expected, dict):
        src_fields = set(expected.get("fields", []))
        for i, mapping in enumerate(mappings):
            if isinstance(mapping, dict) and mapping.get("operation") == "copy" and mapping.get("source_field") not in src_fields:
                errors.append(f"field_mapping[{i}] referencia un campo que no existe en la definición origen.")
    policy = plan.get("policy")
    if not isinstance(policy, dict) or policy.get("strategy") != "copy_to_new_notes" or policy.get("preserve_original_notes") is not True or policy.get("copy_schedule") != "reset" or policy.get("execution") != "plan_only":
        errors.append("policy debe declarar copy_to_new_notes, preserve_original_notes=true, copy_schedule=reset y execution=plan_only.")
    return errors


def normalize_definition(raw: dict[str, Any]) -> dict[str, Any]:
    fields = sorted(raw.get("flds", []) or [], key=lambda f: f.get("ord", 0))
    templates = sorted(raw.get("tmpls", []) or [], key=lambda t: t.get("ord", 0))
    return {
        "kind": "cloze" if raw.get("type") == 1 else "standard",
        "fields": [f.get("name") for f in fields],
        "card_templates": [
            {"name": t.get("name"), "question_html": t.get("qfmt", ""), "answer_html": t.get("afmt", "")}
            for t in templates
        ],
        "css": raw.get("css", "")
    }


def expected_definition(source: dict[str, Any]) -> dict[str, Any]:
    d = source["expected_definition"]
    return {
        "kind": d["kind"],
        "fields": list(d["fields"]),
        "card_templates": [{"name": t["name"], "question_html": t["question_html"], "answer_html": t["answer_html"]} for t in d["card_templates"]],
        "css": d["css"]
    }


def target_definition(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": target["kind"], "fields": list(target["fields"]),
        "card_templates": [{"name": t["name"], "question_html": t["question_html"], "answer_html": t["answer_html"]} for t in target["card_templates"]],
        "css": target["css"]
    }


def definition_diff(source_def: dict[str, Any], target_def: dict[str, Any]) -> dict[str, Any]:
    sf, tf = source_def["fields"], target_def["fields"]
    source_templates = {t["name"]: t for t in source_def["card_templates"]}
    target_templates = {t["name"]: t for t in target_def["card_templates"]}
    changed_templates = []
    for name in sorted(set(source_templates) & set(target_templates)):
        changes = []
        for side, key in (("question", "question_html"), ("answer", "answer_html")):
            if source_templates[name][key] != target_templates[name][key]:
                changes.append(side)
        if changes:
            changed_templates.append({"name": name, "changed_sides": changes})
    common_source = [f for f in sf if f in set(tf)]
    common_target = [f for f in tf if f in set(sf)]
    return {
        "kind_changed": source_def["kind"] != target_def["kind"],
        "fields_added": [f for f in tf if f not in set(sf)],
        "fields_removed": [f for f in sf if f not in set(tf)],
        "field_order_changed": common_source != common_target,
        "source_field_order": sf,
        "target_field_order": tf,
        "templates_added": [n for n in target_templates if n not in source_templates],
        "templates_removed": [n for n in source_templates if n not in target_templates],
        "templates_changed": changed_templates,
        "css_changed": source_def["css"] != target_def["css"],
        "source_template_count": len(source_def["card_templates"]),
        "target_template_count": len(target_def["card_templates"]),
        "strategy": "versioned_copy; existing source model and notes remain unchanged"
    }


def to_migration_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Translate the guarded structural request to the existing migration-plan v0.2 format."""
    return {
        "protocol": "anki-json-migration-plan",
        "migration_plan_version": "0.2.0",
        "plan_id": plan["plan_id"],
        "metadata": plan["metadata"],
        "source": {k: plan["source"][k] for k in ("type_id", "version", "anki_model_name")},
        "target_note_type": plan["target_note_type"],
        "target_deck": plan["target_deck"],
        "selection": plan["selection"],
        "field_mapping": plan["field_mapping"],
        "policy": plan["policy"]
    }
