# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the DynamoDB vector item written by ai-tag-generation.

These lock down the DynamoDB vector-search contract. Getting the item shape
wrong does not raise -- the write succeeds and the asset is simply absent from
or useless in search results -- so the shape is asserted explicitly.
"""
import pytest


class _FakeDynamoDB:
    def __init__(self):
        self.put_item_calls = []

    def put_item(self, **kwargs):
        self.put_item_calls.append(kwargs)
        return {}


@pytest.fixture
def indexer(vector_indexer, monkeypatch):
    """vector_indexer with DynamoDB and Bedrock replaced by fakes."""
    fake_ddb = _FakeDynamoDB()
    monkeypatch.setattr(vector_indexer, "dynamodb_client", fake_ddb)
    monkeypatch.setattr(
        vector_indexer,
        "generate_embedding",
        lambda text: [0.5] * vector_indexer.EMBEDDING_DIMENSIONS,
    )
    monkeypatch.setattr(vector_indexer, "_fake_ddb", fake_ddb, raising=False)
    return vector_indexer


def _written_item(indexer):
    assert len(indexer._fake_ddb.put_item_calls) == 1
    return indexer._fake_ddb.put_item_calls[0]["Item"]


FULL_METADATA = {
    "tags": ["desk", "wooden", "modern"],
    "description": "A modern wooden desk with three drawers",
    "structuredMetadata": {
        "category": "Furniture",
        "subcategory": "Desks",
        "style": "Modern",
        "materials": ["Wood", "Metal"],
        "primaryColors": ["Brown", "Black"],
        "sizeCategory": "large",
    },
}


class TestEmbeddingAttribute:
    def test_embedding_is_a_list_of_numbers(self, indexer):
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        embedding = _written_item(indexer)["embedding"]

        # DynamoDB requires L-of-N here. A Number Set (NS) or a bare list is
        # rejected, and only at write time.
        assert set(embedding) == {"L"}
        assert all(set(v) == {"N"} for v in embedding["L"])
        assert all(isinstance(v["N"], str) for v in embedding["L"])

    def test_embedding_length_matches_the_configured_dimensions(self, indexer):
        # The vector index is created with these dimensions; a write of any
        # other length is rejected.
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        written = _written_item(indexer)["embedding"]["L"]
        assert len(written) == indexer.EMBEDDING_DIMENSIONS


class TestInlineFilterAttributes:
    # These four are INLINE_FILTER entries in the vector index SearchSchema.
    INLINE_FILTERS = ("category", "style", "primaryMaterial", "primaryColor")

    def test_present_when_metadata_is_complete(self, indexer):
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        item = _written_item(indexer)
        for name in self.INLINE_FILTERS:
            assert name in item, f"{name} missing from item"
        assert item["category"]["S"] == "Furniture"
        assert item["style"]["S"] == "Modern"
        assert item["primaryMaterial"]["S"] == "Wood"
        assert item["primaryColor"]["S"] == "Brown"

    def test_present_even_when_the_ai_returned_no_structured_metadata(self, indexer):
        # Every filterable attribute must still be written, so filtered searches
        # behave predictably for sparsely tagged assets.
        indexer.index_asset_vector("asset-1", {"tags": [], "description": ""})
        item = _written_item(indexer)
        for name in self.INLINE_FILTERS:
            assert name in item, f"{name} missing from item"
        assert item["category"]["S"] == "Other"
        assert item["style"]["S"] == "Other"
        assert item["primaryMaterial"]["S"] == ""
        assert item["primaryColor"]["S"] == ""


class TestItemFields:
    def test_partition_key_is_the_asset_id(self, indexer):
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        assert _written_item(indexer)["assetId"] == {"S": "asset-1"}

    def test_writes_to_the_configured_table(self, indexer):
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        call = indexer._fake_ddb.put_item_calls[0]
        assert call["TableName"] == indexer.VECTOR_TABLE

    def test_title_falls_back_to_the_asset_id_when_untagged(self, indexer):
        indexer.index_asset_vector("asset-1", {"tags": [], "description": "d"})
        assert _written_item(indexer)["title"]["S"] == "asset-1"

    def test_description_is_truncated(self, indexer):
        indexer.index_asset_vector(
            "asset-1", {"tags": ["t"], "description": "x" * 5000}
        )
        assert len(_written_item(indexer)["description"]["S"]) == 2000

    def test_tags_are_capped(self, indexer):
        indexer.index_asset_vector(
            "asset-1", {"tags": [f"tag{i}" for i in range(50)], "description": ""}
        )
        assert len(_written_item(indexer)["tags"]["L"]) == 10

    def test_project_id_is_written_only_when_supplied(self, indexer):
        indexer.index_asset_vector("asset-1", FULL_METADATA)
        assert "projectId" not in _written_item(indexer)

        indexer._fake_ddb.put_item_calls.clear()
        indexer.index_asset_vector("asset-1", FULL_METADATA, project_id="proj-9")
        assert _written_item(indexer)["projectId"] == {"S": "proj-9"}

    def test_reports_the_dimensions_it_indexed(self, indexer):
        result = indexer.index_asset_vector("asset-1", FULL_METADATA)
        assert result["indexed"] is True
        assert result["assetId"] == "asset-1"
        assert result["vectorDimensions"] == indexer.EMBEDDING_DIMENSIONS


class TestConfigurationErrors:
    def test_raises_when_the_table_is_not_configured(self, indexer, monkeypatch):
        # Failing loudly beats writing nowhere and reporting success.
        monkeypatch.setattr(indexer, "VECTOR_TABLE", "")
        with pytest.raises(ValueError, match="VECTOR_TABLE_NAME"):
            indexer.index_asset_vector("asset-1", FULL_METADATA)
        assert indexer._fake_ddb.put_item_calls == []
