from typing import Any

import pytest

from aft_mcp.swagger_convert import convert


def swagger(paths: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "swagger": "2.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths,
        **extra,
    }


def ok(**extra: Any) -> dict[str, Any]:
    return {"responses": {"200": {"description": "OK"}}, **extra}


def op_ids(result: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (path, method): op["operationId"]
        for path, item in result["paths"].items()
        for method, op in item.items()
        if isinstance(op, dict) and "operationId" in op
    }


# --- document level ---------------------------------------------------------------


def test_openapi3_spec_passes_through_untouched() -> None:
    spec = {"openapi": "3.0.3", "paths": {}}
    assert convert(spec) is spec


def test_input_is_not_mutated() -> None:
    spec = swagger({"/a": {"get": ok()}}, consumes=["application/json"])
    before = repr(spec)
    convert(spec)
    assert repr(spec) == before


def test_top_level_fields_are_carried_over() -> None:
    spec = swagger(
        {}, tags=[{"name": "x"}], security=[{"basic": []}], externalDocs={"url": "u"}
    )
    result = convert(spec)
    assert result["openapi"] == "3.0.3"
    assert result["tags"] == [{"name": "x"}]
    assert result["security"] == [{"basic": []}]
    assert result["externalDocs"] == {"url": "u"}
    assert "components" not in result


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, [{"url": "/"}]),
        ({"basePath": "/v1"}, [{"url": "/v1"}]),
        ({"host": "h.test"}, [{"url": "https://h.test"}]),
        (
            {"host": "h.test", "basePath": "/api", "schemes": ["http", "https"]},
            [{"url": "http://h.test/api"}, {"url": "https://h.test/api"}],
        ),
    ],
)
def test_servers(extra: dict[str, Any], expected: list[dict[str, str]]) -> None:
    assert convert(swagger({}, **extra))["servers"] == expected


# --- operation ids ----------------------------------------------------------------


def test_operation_ids_are_short_and_unique() -> None:
    result = convert(
        swagger(
            {
                "/api/boards": {"get": ok(), "post": ok()},
                "/api/boards/{board_id}": {"get": ok()},
                "/api/boards/{board_id}/cards": {"get": ok()},
                "/api/cards/{card_id}/comments/{comment_id}": {"delete": ok()},
                "/api/kept": {"get": ok(operationId="mine")},
            }
        )
    )
    assert op_ids(result) == {
        ("/api/boards", "get"): "get_boards",
        ("/api/boards", "post"): "post_boards",
        ("/api/boards/{board_id}", "get"): "get_boards_by_board_id",
        ("/api/boards/{board_id}/cards", "get"): "get_boards_cards",
        ("/api/cards/{card_id}/comments/{comment_id}", "delete"): (
            "delete_cards_comments_by_comment_id"
        ),
        ("/api/kept", "get"): "mine",
    }


def test_colliding_names_get_a_param_then_a_number() -> None:
    result = convert(
        swagger(
            {
                "/api/a/{x}/b": {"get": ok()},
                "/api/a/{y}/b": {"get": ok()},
                "/a/b": {"get": ok()},
                "/a-b": {"get": ok()},
            }
        )
    )
    ids = op_ids(result)
    assert ids[("/api/a/{x}/b", "get")] == "get_a_b"
    assert ids[("/api/a/{y}/b", "get")] == "get_a_b_by_y"
    assert ids[("/a/b", "get")] == "get_a_b_2"
    assert ids[("/a-b", "get")] == "get_a_b_3"


# --- parameters -------------------------------------------------------------------


def test_plain_parameters_get_a_schema_and_path_params_are_required() -> None:
    result = convert(
        swagger(
            {
                "/items/{id}": {
                    "get": ok(
                        parameters=[
                            {"name": "id", "in": "path", "type": "integer"},
                            {
                                "name": "q",
                                "in": "query",
                                "type": "string",
                                "maxLength": 9,
                            },
                        ]
                    )
                }
            }
        )
    )
    params = result["paths"]["/items/{id}"]["get"]["parameters"]
    assert params == [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
        {"name": "q", "in": "query", "schema": {"type": "string", "maxLength": 9}},
    ]


@pytest.mark.parametrize(
    ("location", "fmt", "style", "explode"),
    [
        ("query", "csv", "form", False),
        ("path", "csv", "simple", False),
        ("query", "multi", "form", True),
        ("query", "pipes", "pipeDelimited", False),
        ("query", "ssv", "spaceDelimited", False),
    ],
)
def test_collection_formats(location: str, fmt: str, style: str, explode: bool) -> None:
    param = {
        "name": "ids",
        "in": location,
        "type": "array",
        "items": {"type": "integer"},
        "collectionFormat": fmt,
    }
    result = convert(swagger({"/x/{ids}": {"get": ok(parameters=[param])}}))
    out = result["paths"]["/x/{ids}"]["get"]["parameters"][0]
    assert (out["style"], out["explode"]) == (style, explode)


def test_tsv_has_no_style() -> None:
    param = {"name": "ids", "in": "query", "type": "array", "collectionFormat": "tsv"}
    result = convert(swagger({"/x": {"get": ok(parameters=[param])}}))
    assert "style" not in result["paths"]["/x"]["get"]["parameters"][0]


def test_global_parameter_refs_are_resolved_for_classification() -> None:
    spec = swagger(
        {"/x": {"post": ok(parameters=[{"$ref": "#/parameters/Body"}])}},
        parameters={"Body": {"name": "b", "in": "body", "schema": {"type": "object"}}},
    )
    body = convert(spec)["paths"]["/x"]["post"]["requestBody"]
    assert body["content"] == {"application/json": {"schema": {"type": "object"}}}


def test_path_level_parameters() -> None:
    result = convert(
        swagger(
            {
                "/x/{id}": {
                    "parameters": [
                        {"name": "id", "in": "path", "type": "integer"},
                        {"name": "b", "in": "body", "schema": {"type": "object"}},
                    ],
                    "get": ok(),
                    "put": ok(
                        parameters=[
                            {"name": "b", "in": "body", "schema": {"type": "string"}}
                        ]
                    ),
                }
            }
        )
    )
    item = result["paths"]["/x/{id}"]
    assert [p["name"] for p in item["parameters"]] == ["id"]
    # A path-level body applies to each operation, unless the operation overrides it.
    assert item["get"]["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "object"
    }
    assert item["put"]["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "string"
    }


# --- request bodies ---------------------------------------------------------------


def test_body_parameter_uses_the_consumes_media_types() -> None:
    spec = swagger(
        {
            "/x": {
                "post": ok(
                    consumes=["application/json", "text/plain"],
                    parameters=[
                        {
                            "name": "b",
                            "in": "body",
                            "required": True,
                            "description": "d",
                            "schema": {"type": "object"},
                        }
                    ],
                )
            }
        }
    )
    op = convert(spec)["paths"]["/x"]["post"]
    assert op["requestBody"] == {
        "required": True,
        "description": "d",
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "text/plain": {"schema": {"type": "object"}},
        },
    }
    assert "consumes" not in op


def test_form_parameters_become_a_urlencoded_object() -> None:
    form = [
        {
            "name": "title",
            "in": "formData",
            "type": "string",
            "required": True,
            "description": "t",
        },
        {"name": "n", "in": "formData", "type": "integer"},
    ]
    op = convert(swagger({"/x": {"post": ok(parameters=form)}}))["paths"]["/x"]["post"]
    assert op["requestBody"] == {
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "t"},
                        "n": {"type": "integer"},
                    },
                    "required": ["title"],
                }
            }
        }
    }


def test_file_form_parameter_makes_it_multipart() -> None:
    form = [{"name": "f", "in": "formData", "type": "file"}]
    op = convert(swagger({"/x": {"post": ok(parameters=form)}}))["paths"]["/x"]["post"]
    schema = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema == {
        "type": "object",
        "properties": {"f": {"type": "string", "format": "binary"}},
    }


# --- responses and components -----------------------------------------------------


def test_responses_get_content_examples_and_headers() -> None:
    responses = {
        "200": {
            "description": "OK",
            "schema": {"$ref": "#/definitions/Card"},
            "examples": {"application/json": {"id": 1}, "text/plain": "1"},
            "headers": {"X-Rate": {"type": "integer", "description": "r"}},
        },
        "404": {"$ref": "#/responses/NotFound"},
    }
    spec = swagger(
        {"/x": {"get": {"responses": responses}}},
        definitions={"Card": {"type": "object"}},
        responses={"NotFound": {"description": "missing"}},
    )
    result = convert(spec)
    out = result["paths"]["/x"]["get"]["responses"]
    assert out["200"] == {
        "description": "OK",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Card"},
                "example": {"id": 1},
            },
            "text/plain": {"example": "1"},
        },
        "headers": {"X-Rate": {"description": "r", "schema": {"type": "integer"}}},
    }
    assert out["404"] == {"$ref": "#/components/responses/NotFound"}
    assert result["components"]["schemas"] == {"Card": {"type": "object"}}
    assert result["components"]["responses"] == {"NotFound": {"description": "missing"}}


@pytest.mark.parametrize(
    ("scheme", "expected"),
    [
        ({"type": "basic"}, {"type": "http", "scheme": "basic", "description": ""}),
        (
            {
                "type": "oauth2",
                "flow": "accessCode",
                "authorizationUrl": "a",
                "tokenUrl": "t",
                "description": "d",
            },
            {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": "a",
                        "tokenUrl": "t",
                        "scopes": {},
                    }
                },
                "description": "d",
            },
        ),
        (
            {"type": "oauth2", "flow": "unknown", "scopes": {"r": "read"}},
            {"type": "oauth2", "flows": {"implicit": {"scopes": {"r": "read"}}}},
        ),
        (
            {"type": "apiKey", "name": "X-Key", "in": "header"},
            {"type": "apiKey", "name": "X-Key", "in": "header"},
        ),
    ],
)
def test_security_schemes(scheme: dict[str, Any], expected: dict[str, Any]) -> None:
    result = convert(swagger({}, securityDefinitions={"s": scheme}))
    assert result["components"]["securitySchemes"]["s"] == expected


def test_swagger_only_schema_idioms_are_normalised() -> None:
    definitions = {
        "Card": {
            "type": "object",
            "properties": {
                "due": {"type": "string", "x-nullable": True},
                "odd": {"type": "string", "x-nullable": "yes"},
                "blob": {"type": "file"},
            },
        }
    }
    props = convert(swagger({}, definitions=definitions))["components"]["schemas"][
        "Card"
    ]["properties"]
    assert props["due"] == {"type": "string", "nullable": True}
    assert props["odd"] == {"type": "string"}
    assert props["blob"] == {"type": "string", "format": "binary"}


def test_external_parameter_refs_pass_through() -> None:
    ref = {"$ref": "other.json#/Id"}
    result = convert(swagger({"/x": {"get": ok(parameters=[ref])}}))
    assert result["paths"]["/x"]["get"]["parameters"] == [ref]
