"""A validator for the JSON Schema keywords that `schemas/report.schema.json` uses.

No JSON Schema package is pinned for this project, so the published schema is checked here.
A keyword this validator does not implement raises instead of passing, so a schema change that
adds a new constraint cannot slip through silently.
"""

from datetime import datetime
from typing import Any

_ANNOTATIONS = {"$defs", "$schema", "$id", "title", "default", "description", "examples"}
_KEYWORDS = _ANNOTATIONS | {
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "enum",
    "format",
    "items",
    "maxLength",
    "minItems",
    "minLength",
    "minimum",
    "properties",
    "propertyNames",
    "required",
    "type",
}
_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate_document(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Every violation as a readable string; an empty list means the instance is valid."""
    return _check(instance, schema, schema, "$")


def _resolve(ref: str, root: dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        raise NotImplementedError(f"only local references are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value: Any, schema: Any, root: dict[str, Any], path: str) -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here"]
    unknown = set(schema) - _KEYWORDS
    if unknown:
        raise NotImplementedError(f"unsupported schema keywords at {path}: {sorted(unknown)}")
    errors: list[str] = []
    if "$ref" in schema:
        errors += _check(value, _resolve(schema["$ref"], root), root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPE_CHECKS[name](value) for name in types):
            found = type(value).__name__
            return [*errors, f"{path}: expected {' or '.join(types)}, got {found}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of the allowed values")
    if "anyOf" in schema:
        branches = [_check(value, s, root, path) for s in schema["anyOf"]]
        if all(branches):
            reasons = "; ".join(branch[0] for branch in branches)
            errors.append(f"{path}: matches none of the allowed shapes ({reasons})")
    if isinstance(value, str):
        errors += _check_string(value, schema, path)
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below the minimum {schema['minimum']}")
    if isinstance(value, list):
        errors += _check_list(value, schema, root, path)
    if isinstance(value, dict):
        errors += _check_object(value, schema, root, path)
    return errors


def _check_string(value: str, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: shorter than {schema['minLength']} characters")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{path}: longer than {schema['maxLength']} characters")
    fmt = schema.get("format")
    if fmt == "date-time":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{path}: {value!r} is not a date-time")
    elif fmt is not None:
        raise NotImplementedError(f"unsupported format at {path}: {fmt}")
    return errors


def _check_list(value: list[Any], schema: dict[str, Any], root: dict[str, Any], path: str):
    errors: list[str] = []
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path}: fewer than {schema['minItems']} items")
    if "items" in schema:
        for index, item in enumerate(value):
            errors += _check(item, schema["items"], root, f"{path}[{index}]")
    return errors


def _check_object(value: dict[str, Any], schema: dict[str, Any], root: dict[str, Any], path: str):
    errors: list[str] = []
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{path}: missing required property '{name}'")
    if "propertyNames" in schema:
        for key in value:
            errors += _check(key, schema["propertyNames"], root, f"{path} (key {key!r})")
    properties = schema.get("properties", {})
    for key, item in value.items():
        if key in properties:
            errors += _check(item, properties[key], root, f"{path}.{key}")
        elif "additionalProperties" in schema:
            extra = schema["additionalProperties"]
            if extra is False:
                errors.append(f"{path}: unexpected property '{key}'")
            elif extra is not True:
                errors += _check(item, extra, root, f"{path}.{key}")
    return errors
