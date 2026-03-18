# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the shared embedding helper."""
import json


class _FakeBody:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class _FakeBedrock:
    """Records invoke_model calls and returns a fixed embedding."""

    def __init__(self, dimensions=1024):
        self.calls = []
        self._dimensions = dimensions

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        embedding = [0.1] * self._dimensions
        return {"body": _FakeBody({"embedding": embedding})}


class TestBuildSearchText:
    def test_joins_title_description_and_tags(self, vector_utils):
        text = vector_utils.build_search_text(
            "Oak Desk", "A wooden desk with three drawers", ["desk", "wood"]
        )
        assert text == "Oak Desk. A wooden desk with three drawers. Tags: desk, wood"

    def test_omits_missing_parts_without_leaving_separators(self, vector_utils):
        # A blank description must not produce a doubled ". ." separator, which
        # would add noise to the embedded text.
        assert vector_utils.build_search_text("Oak Desk", "", ["desk"]) == (
            "Oak Desk. Tags: desk"
        )
        assert vector_utils.build_search_text("", "A desk", None) == "A desk"

    def test_returns_empty_string_when_nothing_is_provided(self, vector_utils):
        assert vector_utils.build_search_text("", "", None) == ""

    def test_empty_tag_list_adds_no_tag_section(self, vector_utils):
        assert vector_utils.build_search_text("Oak Desk", "A desk", []) == (
            "Oak Desk. A desk"
        )


class TestGenerateEmbedding:
    def test_requests_the_configured_model_and_dimensions(
        self, vector_utils, monkeypatch
    ):
        # The index is created with EmbeddingDimensions, so a mismatch here
        # silently produces unusable vectors.
        fake = _FakeBedrock(dimensions=vector_utils.EMBEDDING_DIMENSIONS)
        monkeypatch.setattr(vector_utils, "get_bedrock_runtime", lambda: fake)

        embedding = vector_utils.generate_embedding("a wooden desk")

        assert len(embedding) == vector_utils.EMBEDDING_DIMENSIONS
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["modelId"] == vector_utils.EMBEDDING_MODEL_ID
        body = json.loads(call["body"])
        assert body["inputText"] == "a wooden desk"
        assert body["dimensions"] == vector_utils.EMBEDDING_DIMENSIONS
        # COSINE tolerates unnormalized vectors, but normalizing keeps the
        # option of switching the index to DOT_PRODUCT.
        assert body["normalize"] is True

    def test_explicit_dimensions_override_the_default(
        self, vector_utils, monkeypatch
    ):
        fake = _FakeBedrock(dimensions=256)
        monkeypatch.setattr(vector_utils, "get_bedrock_runtime", lambda: fake)

        vector_utils.generate_embedding("a wooden desk", dimensions=256)

        assert json.loads(fake.calls[0]["body"])["dimensions"] == 256

    def test_truncates_input_that_exceeds_the_model_limit(
        self, vector_utils, monkeypatch
    ):
        fake = _FakeBedrock(dimensions=vector_utils.EMBEDDING_DIMENSIONS)
        monkeypatch.setattr(vector_utils, "get_bedrock_runtime", lambda: fake)

        vector_utils.generate_embedding("x" * 40000)

        # Titan Embed v2 rejects input beyond its token limit, so over-long text
        # must be cut before the call rather than surfacing as a 400.
        assert len(json.loads(fake.calls[0]["body"])["inputText"]) == 30000
