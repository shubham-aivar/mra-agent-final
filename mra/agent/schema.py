"""A small checker for tool arguments. The model's arguments are untrusted input like any other."""
from __future__ import annotations


def _check(v, spec: dict, name: str) -> str | None:
    t = spec["type"]
    if t == "string":
        if not isinstance(v, str):
            return f"'{name}' must be text"
        if len(v) < spec.get("minLength", 0) or len(v) > spec.get("maxLength", 10_000):
            return f"'{name}' must be {spec.get('minLength', 0)} to {spec.get('maxLength', 10_000)} characters (got {len(v)})"
        if "enum" in spec and v not in spec["enum"]:
            return f"'{name}' must be one of {spec['enum']}"
    elif t == "integer":
        if isinstance(v, bool) or not isinstance(v, int):
            return f"'{name}' must be a whole number"
        if not spec.get("minimum", v) <= v <= spec.get("maximum", v):
            return f"'{name}' must be between {spec.get('minimum')} and {spec.get('maximum')}"
    elif t == "array":
        if not isinstance(v, list):
            return f"'{name}' must be a list"
        if not spec.get("minItems", 0) <= len(v) <= spec.get("maxItems", 1000):
            return f"'{name}' must have {spec.get('minItems', 0)} to {spec.get('maxItems', 1000)} items"
        for i, item in enumerate(v):
            err = _check(item, spec["items"], f"{name}[{i}]")
            if err:
                return err
    return None


def validate(args, schema: dict) -> str | None:
    """Return a plain-text problem, or None if the arguments are acceptable."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = schema.get("properties", {})
    for k in schema.get("required", []):
        if k not in args:
            return f"missing '{k}'"
    for k, v in args.items():
        if k not in props:
            return f"unknown argument '{k}'"
        err = _check(v, props[k], k)
        if err:
            return err
    return None
