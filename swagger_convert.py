"""Minimal Swagger 2.0 -> OpenAPI 3.0 converter.

A small Python stand-in for swagger2openapi.
"""

import copy
from typing import Any

type JSON = dict[str, Any]

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SCHEMA_KEYS = (
    "type",
    "format",
    "items",
    "enum",
    "default",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maxLength",
    "minLength",
    "pattern",
    "maxItems",
    "minItems",
    "uniqueItems",
    "multipleOf",
    "x-nullable",
)
DEFAULT_MEDIA_TYPE = "application/json"
FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
MULTIPART_MEDIA_TYPE = "multipart/form-data"

# Swagger 2.0 collectionFormat -> OpenAPI 3.0 (style, explode) by parameter location.
# tsv has no 3.0 equivalent and is left to the default style.
_COLLECTION_FORMATS = {
    "csv": {
        "query": ("form", False),
        "cookie": ("form", False),
        "path": ("simple", False),
        "header": ("simple", False),
    },
    "ssv": {"query": ("spaceDelimited", False)},
    "pipes": {"query": ("pipeDelimited", False)},
    "multi": {"query": ("form", True), "cookie": ("form", True)},
}
_OAUTH_FLOWS = {
    "implicit": "implicit",
    "password": "password",
    "application": "clientCredentials",
    "accessCode": "authorizationCode",
}


def convert(spec: JSON) -> JSON:
    """Return an OpenAPI 3.0 document for a Swagger 2.0 spec.

    Specs that are already OpenAPI 3.x pass through unchanged.
    """
    if not str(spec.get("swagger", "")).startswith("2"):
        return spec

    spec = copy.deepcopy(spec)
    global_consumes = spec.pop("consumes", None)
    global_produces = spec.pop("produces", None)
    global_params = spec.get("parameters", {})

    result: JSON = {
        "openapi": "3.0.3",
        "info": spec.get("info", {}),
        "servers": _servers(spec),
        "paths": {
            path: _path_item(item, global_params, global_consumes, global_produces)
            for path, item in spec.get("paths", {}).items()
        },
    }

    _assign_operation_ids(result["paths"])

    components: JSON = {}
    if spec.get("definitions"):
        components["schemas"] = spec["definitions"]
    if spec.get("responses"):
        components["responses"] = {
            name: _response(r, global_produces or [DEFAULT_MEDIA_TYPE])
            for name, r in spec["responses"].items()
        }
    if spec.get("securityDefinitions"):
        components["securitySchemes"] = {
            name: _security_scheme(s) for name, s in spec["securityDefinitions"].items()
        }
    if components:
        result["components"] = components
    for key in ("security", "tags", "externalDocs"):
        if key in spec:
            result[key] = spec[key]

    converted: JSON = _normalise(_rewrite_refs(result))
    return converted


def _assign_operation_ids(paths: JSON) -> None:
    """Give every operation lacking an operationId a short one, e.g. get_boards_cards.

    Literal path segments form the name and {params} are dropped. A path ending in a
    {param} (a single item) gets '_by_<param>' appended, as does any other name that
    would collide; a numeric suffix is the last resort.
    """
    taken = {
        op["operationId"]
        for item in paths.values()
        for m, op in item.items()
        if m in HTTP_METHODS and "operationId" in op
    }
    for path, item in paths.items():
        literals, params, trailing = _path_words(path)
        ends_in_param = bool(trailing)
        for method, op in item.items():
            if method not in HTTP_METHODS or "operationId" in op:
                continue
            name = "_".join([method, *literals])
            if params and (ends_in_param or name in taken):
                name = f"{name}_by_{'_and_'.join(trailing or params[-1:])}"
            base, n = name, 2
            while name in taken:
                name, n = f"{base}_{n}", n + 1
            taken.add(name)
            op["operationId"] = name


def _path_words(path: str) -> tuple[list[str], list[str], list[str]]:
    """Split a path into its literal words, its {params}, and the params ending it.

    A leading 'api' segment is dropped, and every word is slugged.
    """
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
    return literals, params, trailing


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def _servers(spec: JSON) -> list[JSON]:
    base_path = spec.get("basePath", "/")
    host = spec.get("host")
    if not host:
        return [{"url": base_path}]
    schemes = spec.get("schemes") or ["https"]
    return [
        {"url": f"{scheme}://{host}{base_path}".rstrip("/") or "/"}
        for scheme in schemes
    ]


def _security_scheme(scheme: JSON) -> JSON:
    kind = scheme.get("type")
    if kind == "basic":
        return {
            "type": "http",
            "scheme": "basic",
            "description": scheme.get("description", ""),
        }
    if kind == "oauth2":
        flow: JSON = {
            k: scheme[k] for k in ("authorizationUrl", "tokenUrl") if k in scheme
        }
        flow["scopes"] = scheme.get("scopes", {})
        out: JSON = {
            "type": "oauth2",
            "flows": {_OAUTH_FLOWS.get(str(scheme.get("flow")), "implicit"): flow},
        }
        if "description" in scheme:
            out["description"] = scheme["description"]
        return out
    return scheme


def _deref_param(param: JSON, global_params: JSON) -> JSON:
    """Inline a '#/parameters/X' reference so the parameter can be classified."""
    ref = param.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/parameters/"):
        resolved: JSON = global_params[ref.rsplit("/", 1)[1]]
        return resolved
    return param


def _path_item(
    item: JSON,
    global_params: JSON,
    global_consumes: list[str] | None,
    global_produces: list[str] | None,
) -> JSON:
    shared_params = [_deref_param(p, global_params) for p in item.get("parameters", [])]
    out = {k: v for k, v in item.items() if k not in HTTP_METHODS and k != "parameters"}
    plain = [
        _parameter(p) for p in shared_params if p.get("in") not in ("body", "formData")
    ]
    if plain:
        out["parameters"] = plain
    # Path-level body/formData parameters have no 3.0 path-level home, so they
    # apply to each operation.
    inherited = [p for p in shared_params if p.get("in") in ("body", "formData")]
    for method, operation in item.items():
        if method in HTTP_METHODS:
            out[method] = _operation(
                operation, inherited, global_params, global_consumes, global_produces
            )
    return out


def _operation(
    op: JSON,
    inherited: list[JSON],
    global_params: JSON,
    global_consumes: list[str] | None,
    global_produces: list[str] | None,
) -> JSON:
    consumes = op.get("consumes") or global_consumes or [DEFAULT_MEDIA_TYPE]
    produces = op.get("produces") or global_produces or [DEFAULT_MEDIA_TYPE]

    own = [_deref_param(p, global_params) for p in op.get("parameters", [])]
    overridden = {(p.get("name"), p.get("in")) for p in own}
    params = [
        p for p in inherited if (p.get("name"), p.get("in")) not in overridden
    ] + own
    body = next((p for p in params if p.get("in") == "body"), None)
    form = [p for p in params if p.get("in") == "formData"]

    out = {
        k: v
        for k, v in op.items()
        if k not in ("parameters", "responses", "consumes", "produces")
    }
    plain = [_parameter(p) for p in own if p.get("in") not in ("body", "formData")]
    if plain:
        out["parameters"] = plain

    if body:
        out["requestBody"] = {
            "required": body.get("required", False),
            "description": body.get("description", ""),
            "content": {mt: {"schema": body.get("schema", {})} for mt in consumes},
        }
    elif form:
        multipart = MULTIPART_MEDIA_TYPE in consumes or any(
            p.get("type") == "file" for p in form
        )
        schema: JSON = {
            "type": "object",
            "properties": {p["name"]: _form_property(p) for p in form},
        }
        required = [p["name"] for p in form if p.get("required")]
        if required:
            schema["required"] = required
        out["requestBody"] = {
            "content": {
                (MULTIPART_MEDIA_TYPE if multipart else FORM_MEDIA_TYPE): {
                    "schema": schema
                }
            }
        }

    out["responses"] = {
        code: _response(resp, produces)
        for code, resp in op.get("responses", {}).items()
    }
    return out


def _schema_from(param: JSON) -> JSON:
    schema: JSON = {k: param[k] for k in SCHEMA_KEYS if k in param}
    if schema.get("type") == "file":
        schema = {"type": "string", "format": "binary"}
    return schema


def _parameter(param: JSON) -> JSON:
    if "$ref" in param:
        return param
    out: JSON = {
        k: param[k]
        for k in ("name", "in", "description", "required", "allowEmptyValue")
        if k in param
    }
    if param["in"] == "path":
        out["required"] = True
    out["schema"] = param.get("schema") or _schema_from(param)
    if param.get("type") == "array":
        style, explode = _COLLECTION_FORMATS.get(
            param.get("collectionFormat", "csv"), {}
        ).get(param["in"], (None, None))
        if style:
            out["style"], out["explode"] = style, explode
    return out


def _form_property(param: JSON) -> JSON:
    prop = _schema_from(param)
    if "description" in param:
        prop["description"] = param["description"]
    return prop


def _response(resp: JSON, produces: list[str]) -> JSON:
    if "$ref" in resp:
        return resp
    out: JSON = {"description": resp.get("description", "")}
    examples = resp.get("examples", {})
    if "schema" in resp:
        out["content"] = {mt: {"schema": resp["schema"]} for mt in produces}
    if examples:
        content = out.setdefault("content", {})
        for mt, example in examples.items():
            content.setdefault(mt, {})["example"] = example
    if "headers" in resp:
        out["headers"] = {
            name: {"description": h.get("description", ""), "schema": _schema_from(h)}
            for name, h in resp["headers"].items()
        }
    return out


def _rewrite_refs(node: Any) -> Any:  # noqa: ANN401 - walks arbitrary JSON
    """Point '#/definitions/X' refs at '#/components/schemas/X'.

    '#/responses/X' refs likewise move to '#/components/responses/X'.
    """
    if isinstance(node, dict):
        return {
            k: (
                _rewrite_ref(v)
                if k == "$ref" and isinstance(v, str)
                else _rewrite_refs(v)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node


def _rewrite_ref(ref: str) -> str:
    return ref.replace("#/definitions/", "#/components/schemas/").replace(
        "#/responses/", "#/components/responses/"
    )


def _normalise(node: Any) -> Any:  # noqa: ANN401 - walks arbitrary JSON
    """Rewrite Swagger 2.0 schema idioms that are not valid in 3.0, anywhere.

    'x-nullable' becomes 'nullable', and a leftover 'type: file' (a schema, not a
    parameter) becomes a binary string.
    """
    if isinstance(node, dict):
        out = {k: _normalise(v) for k, v in node.items()}
        if "x-nullable" in out:
            nullable = out.pop("x-nullable")
            if isinstance(nullable, bool):
                out.setdefault("nullable", nullable)
        if out.get("type") == "file":
            out["type"], out["format"] = "string", "binary"
        return out
    if isinstance(node, list):
        return [_normalise(v) for v in node]
    return node
