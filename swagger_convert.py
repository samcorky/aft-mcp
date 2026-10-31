"""Minimal Swagger 2.0 -> OpenAPI 3.0 converter (a small Python stand-in for swagger2openapi)."""

import copy

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SCHEMA_KEYS = (
    "type", "format", "items", "enum", "default", "maximum", "minimum",
    "exclusiveMaximum", "exclusiveMinimum", "maxLength", "minLength",
    "pattern", "maxItems", "minItems", "uniqueItems", "multipleOf",
)
DEFAULT_MEDIA_TYPE = "application/json"
FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
MULTIPART_MEDIA_TYPE = "multipart/form-data"


def convert(spec: dict) -> dict:
    """Return an OpenAPI 3.0 document for a Swagger 2.0 spec (3.x specs pass through)."""
    if not str(spec.get("swagger", "")).startswith("2"):
        return spec

    spec = copy.deepcopy(spec)
    global_consumes = spec.pop("consumes", None)
    global_produces = spec.pop("produces", None)

    result: dict = {
        "openapi": "3.0.3",
        "info": spec.get("info", {}),
        "servers": _servers(spec),
        "paths": {
            path: _path_item(item, global_consumes, global_produces)
            for path, item in spec.get("paths", {}).items()
        },
    }

    _assign_operation_ids(result["paths"])

    components: dict = {}
    if spec.get("definitions"):
        components["schemas"] = spec["definitions"]
    if spec.get("securityDefinitions"):
        components["securitySchemes"] = {
            name: _security_scheme(s) for name, s in spec["securityDefinitions"].items()
        }
    if components:
        result["components"] = components
    for key in ("security", "tags", "externalDocs"):
        if key in spec:
            result[key] = spec[key]

    return _rewrite_refs(result)


def _assign_operation_ids(paths: dict) -> None:
    """Give every operation lacking an operationId a short one, e.g. get_boards_cards.

    Literal path segments form the name and {params} are dropped. A path ending in a
    {param} (a single item) gets '_by_<param>' appended, as does any other name that
    would collide; a numeric suffix is the last resort.
    """
    taken = {op["operationId"] for item in paths.values() for m, op in item.items() if m in HTTP_METHODS and "operationId" in op}
    for path, item in paths.items():
        words = [s for s in path.strip("/").split("/") if s]
        if words and words[0] == "api":
            words = words[1:]
        literals = [_slug(w) for w in words if not w.startswith("{")]
        params = [_slug(w.strip("{}")) for w in words if w.startswith("{")]
        trailing: list[str] = []
        for w in reversed(words):
            if not w.startswith("{"):
                break
            trailing.insert(0, _slug(w.strip("{}")))
        for method, op in item.items():
            if method not in HTTP_METHODS or "operationId" in op:
                continue
            name = "_".join([method, *literals])
            if params and (words[-1].startswith("{") or name in taken):
                name = f"{name}_by_{'_and_'.join(trailing or params[-1:])}"
            base, n = name, 2
            while name in taken:
                name, n = f"{base}_{n}", n + 1
            taken.add(name)
            op["operationId"] = name


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def _servers(spec: dict) -> list[dict]:
    base_path = spec.get("basePath", "/")
    host = spec.get("host")
    if not host:
        return [{"url": base_path}]
    schemes = spec.get("schemes") or ["https"]
    return [{"url": f"{scheme}://{host}{base_path}".rstrip("/") or "/"} for scheme in schemes]


def _security_scheme(scheme: dict) -> dict:
    if scheme.get("type") == "basic":
        return {"type": "http", "scheme": "basic", "description": scheme.get("description", "")}
    return scheme


def _path_item(item: dict, global_consumes, global_produces) -> dict:
    shared_params = item.get("parameters", [])
    out = {k: v for k, v in item.items() if k not in HTTP_METHODS and k != "parameters"}
    if shared_params:
        out["parameters"] = [_parameter(p) for p in shared_params if p.get("in") not in ("body", "formData")]
    for method, operation in item.items():
        if method in HTTP_METHODS:
            out[method] = _operation(operation, shared_params, global_consumes, global_produces)
    return out


def _operation(op: dict, shared_params: list, global_consumes, global_produces) -> dict:
    consumes = op.get("consumes") or global_consumes or [DEFAULT_MEDIA_TYPE]
    produces = op.get("produces") or global_produces or [DEFAULT_MEDIA_TYPE]

    params = [p for p in op.get("parameters", [])]
    body = next((p for p in params if p["in"] == "body"), None)
    form = [p for p in params if p["in"] == "formData"]

    out = {k: v for k, v in op.items() if k not in ("parameters", "responses", "consumes", "produces")}
    plain = [_parameter(p) for p in params if p["in"] not in ("body", "formData")]
    if plain:
        out["parameters"] = plain

    if body:
        out["requestBody"] = {
            "required": body.get("required", False),
            "description": body.get("description", ""),
            "content": {mt: {"schema": body.get("schema", {})} for mt in consumes},
        }
    elif form:
        multipart = MULTIPART_MEDIA_TYPE in consumes or any(p.get("type") == "file" for p in form)
        out["requestBody"] = {
            "content": {
                (MULTIPART_MEDIA_TYPE if multipart else FORM_MEDIA_TYPE): {
                    "schema": {
                        "type": "object",
                        "properties": {p["name"]: _form_property(p) for p in form},
                        "required": [p["name"] for p in form if p.get("required")],
                    }
                }
            }
        }
        if not out["requestBody"]["content"][next(iter(out["requestBody"]["content"]))]["schema"]["required"]:
            del out["requestBody"]["content"][next(iter(out["requestBody"]["content"]))]["schema"]["required"]

    out["responses"] = {
        code: _response(resp, produces) for code, resp in op.get("responses", {}).items()
    }
    return out


def _schema_from(param: dict) -> dict:
    schema = {k: param[k] for k in SCHEMA_KEYS if k in param}
    if schema.get("type") == "file":
        schema = {"type": "string", "format": "binary"}
    return schema


def _parameter(param: dict) -> dict:
    out = {
        k: param[k] for k in ("name", "in", "description", "required", "allowEmptyValue") if k in param
    }
    if param["in"] == "path":
        out["required"] = True
    out["schema"] = param.get("schema") or _schema_from(param)
    return out


def _form_property(param: dict) -> dict:
    prop = _schema_from(param)
    if "description" in param:
        prop["description"] = param["description"]
    return prop


def _response(resp: dict, produces: list[str]) -> dict:
    out = {"description": resp.get("description", "")}
    if "schema" in resp:
        out["content"] = {mt: {"schema": resp["schema"]} for mt in produces}
    if "headers" in resp:
        out["headers"] = {
            name: {"description": h.get("description", ""), "schema": _schema_from(h)}
            for name, h in resp["headers"].items()
        }
    return out


def _rewrite_refs(node):
    """Point '#/definitions/X' refs at '#/components/schemas/X'."""
    if isinstance(node, dict):
        return {
            k: (v.replace("#/definitions/", "#/components/schemas/") if k == "$ref" and isinstance(v, str) else _rewrite_refs(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node
