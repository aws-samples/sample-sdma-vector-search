# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the vector search API handler."""
import json

import pytest
from botocore.exceptions import ClientError


class _FakeDynamoDB:
    def __init__(self, results=None):
        self.search_vectors_calls = []
        self._results = results if results is not None else []
        # Successive Scan responses, so a test can hand back several pages.
        self.scan_pages = None
        self.scan_calls = []

    def search_vectors(self, **kwargs):
        self.search_vectors_calls.append(kwargs)
        return {"SearchResults": self._results}

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        if self.scan_pages is None:
            return {"Items": []}
        return self.scan_pages[min(len(self.scan_calls) - 1,
                                   len(self.scan_pages) - 1)]


@pytest.fixture
def api(search_handler, monkeypatch):
    """search_handler with DynamoDB and Bedrock replaced by fakes."""
    fake_ddb = _FakeDynamoDB()
    monkeypatch.setattr(search_handler, "dynamodb_client", fake_ddb)
    monkeypatch.setattr(
        search_handler,
        "generate_embedding",
        lambda text: [0.25] * search_handler.EMBEDDING_DIMENSIONS,
    )
    monkeypatch.setattr(search_handler, "_fake_ddb", fake_ddb, raising=False)
    return search_handler


def _body(response):
    return json.loads(response["body"])


class TestBuildSearchCondition:
    def test_no_filters_yields_no_condition(self, api):
        assert api.build_search_condition({}) == (None, None, None)

    def test_filter_values_that_are_all_empty_yield_no_condition(self, api):
        assert api.build_search_condition(
            {"category": "", "style": None, "materials": []}
        ) == (None, None, None)

    def test_single_filter_uses_equality(self, api):
        # Inline filters support '=' only; range and IN operators are rejected.
        condition, values, names = api.build_search_condition({"category": "Furniture"})
        assert condition == "category = :category"
        assert values == {":category": {"S": "Furniture"}}

    def test_multiple_filters_are_joined_with_and(self, api):
        condition, values, names = api.build_search_condition(
            {"category": "Furniture", "style": "Modern"}
        )
        # 'style' is a DynamoDB reserved word, so it must be aliased. Sending
        # it bare made every ranked search with a style filter return HTTP 500.
        assert condition == "category = :category AND #style = :style"
        assert values == {
            ":category": {"S": "Furniture"},
            ":style": {"S": "Modern"},
        }
        assert names == {"#style": "style"}

    def test_list_valued_material_collapses_to_its_first_entry(self, api):
        # The index stores one primaryMaterial per asset, so a multi-select in
        # the UI can only be applied as its first choice.
        condition, values, names = api.build_search_condition(
            {"materials": ["Wood", "Metal"]}
        )
        assert condition == "primaryMaterial = :primaryMaterial"
        assert values == {":primaryMaterial": {"S": "Wood"}}

    def test_list_valued_color_collapses_to_its_first_entry(self, api):
        condition, values, names = api.build_search_condition(
            {"primaryColors": ["Brown", "Black"]}
        )
        assert condition == "primaryColor = :primaryColor"
        assert values == {":primaryColor": {"S": "Brown"}}

    def test_only_the_documented_filter_keys_are_read(self, api):
        # The stored attribute names -- primaryMaterial, primaryColor -- used to
        # be accepted as extra aliases for the documented `materials` and
        # `primaryColors`. That was undocumented surface no client sent, so a
        # request using them now filters on nothing.
        condition, values, _ = api.build_search_condition(
            {"primaryMaterial": "Metal", "primaryColor": "Black"}
        )
        assert condition is None
        assert values is None

    def test_only_inline_filter_attributes_reach_the_expression(self, api):
        # Anything outside the index's four INLINE_FILTER attributes makes
        # SearchVectors reject the whole request, so projectId, subcategory and
        # sizeCategory must stay out of it and be applied to the results.
        condition, _, _ = api.build_search_condition(
            {"projectId": "p-1", "subcategory": "Chair", "sizeCategory": "large"}
        )
        assert condition is None

    def test_reserved_word_is_aliased_on_its_own(self, api):
        condition, values, names = api.build_search_condition({"style": "Modern"})
        assert condition == "#style = :style"
        assert names == {"#style": "style"}
        assert values == {":style": {"S": "Modern"}}


class TestSearchVectorsRequest:
    def test_search_vector_is_a_bare_list_not_a_dynamodb_list(self, api):
        api.search_assets("a wooden desk")
        params = api._fake_ddb.search_vectors_calls[0]

        # SearchVectors takes a plain array of {"N": ...}. Wrapping it in an
        # L type -- as item attributes require -- is rejected. The two shapes
        # are easy to conflate because the same vector is stored as L on write.
        vector = params["SearchVector"]
        assert isinstance(vector, list)
        assert len(vector) == api.EMBEDDING_DIMENSIONS
        assert all(set(v) == {"N"} for v in vector)

    def test_targets_the_configured_table_and_index(self, api):
        api.search_assets("a wooden desk")
        params = api._fake_ddb.search_vectors_calls[0]
        assert params["TableName"] == api.VECTOR_TABLE
        assert params["IndexName"] == api.VECTOR_INDEX_NAME

    def test_top_k_is_passed_through(self, api):
        api.search_assets("a wooden desk", top_k=7)
        assert api._fake_ddb.search_vectors_calls[0]["TopK"] == 7

    def test_filter_parameters_are_omitted_when_there_are_no_filters(self, api):
        api.search_assets("a wooden desk")
        params = api._fake_ddb.search_vectors_calls[0]
        # Sending an empty expression is a validation error, so both keys must
        # be absent rather than empty.
        assert "SearchConditionExpression" not in params
        assert "ExpressionAttributeValues" not in params

    def test_filter_parameters_are_included_when_filters_are_given(self, api):
        api.search_assets("a wooden desk", {"category": "Furniture"})
        params = api._fake_ddb.search_vectors_calls[0]
        assert params["SearchConditionExpression"] == "category = :category"
        assert params["ExpressionAttributeValues"] == {
            ":category": {"S": "Furniture"}
        }

    def test_raises_when_the_table_is_not_configured(self, api, monkeypatch):
        monkeypatch.setattr(api, "VECTOR_TABLE", "")
        with pytest.raises(ValueError, match="VECTOR_TABLE_NAME"):
            api.search_assets("a wooden desk")


class TestRouting:
    def test_search_returns_results(self, api, monkeypatch):
        monkeypatch.setattr(api, "search_assets", lambda q, f, k: [{"assetId": "a1"}])
        response = api.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/assets/search",
                "body": json.dumps({"query": "desk"}),
            },
            None,
        )
        assert response["statusCode"] == 200
        body = _body(response)
        assert body["success"] is True
        assert body["resultCount"] == 1
        assert body["results"] == [{"assetId": "a1"}]

    def test_search_without_a_query_browses_instead_of_failing(self, api, monkeypatch):
        # Browsing used to be impossible: every path embedded the query, so the
        # UI sent '*' and got results ranked by their distance to that
        # character. A queryless request now lists assets instead.
        monkeypatch.setattr(api, "browse_assets", lambda f, k: [{"assetId": "a1"}])
        response = api.lambda_handler(
            {"httpMethod": "POST", "path": "/assets/search", "body": "{}"}, None
        )
        assert response["statusCode"] == 200
        body = _body(response)
        assert body["mode"] == "browse"
        assert body["query"] is None
        assert body["results"] == [{"assetId": "a1"}]

    def test_blank_query_browses_rather_than_embedding_whitespace(self, api, monkeypatch):
        monkeypatch.setattr(api, "browse_assets", lambda f, k: [])
        monkeypatch.setattr(
            api, "search_assets",
            lambda *a: pytest.fail("a blank query must not reach the embedder"),
        )
        response = api.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/assets/search",
                "body": json.dumps({"query": "   "}),
            },
            None,
        )
        assert _body(response)["mode"] == "browse"

    def test_browse_passes_filters_through(self, api, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            api, "browse_assets",
            lambda f, k: seen.update(filters=f, limit=k) or [],
        )
        api.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/assets/search",
                "body": json.dumps({"filters": {"category": "Furniture"}, "limit": 24}),
            },
            None,
        )
        assert seen == {"filters": {"category": "Furniture"}, "limit": 24}

    def test_search_marks_results_as_ranked(self, api, monkeypatch):
        monkeypatch.setattr(api, "search_assets", lambda q, f, k: [])
        response = api.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/assets/search",
                "body": json.dumps({"query": "desk"}),
            },
            None,
        )
        assert _body(response)["mode"] == "semantic"

    def test_legacy_search_assets_path_is_no_longer_routed(self, api):
        # Removed in favour of the documented /assets/search. Kept as a test so
        # the alias is not reintroduced without also documenting it.
        response = api.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/search-assets",
                "body": json.dumps({"query": "desk"}),
            },
            None,
        )
        assert response["statusCode"] == 404

    def test_status_requires_ids(self, api):
        response = api.lambda_handler(
            {"httpMethod": "GET", "path": "/assets/status"}, None
        )
        assert response["statusCode"] == 400
        assert "ids" in _body(response)["error"]

    def test_status_rejects_more_than_one_hundred_ids(self, api):
        response = api.lambda_handler(
            {
                "httpMethod": "GET",
                "path": "/assets/status",
                "queryStringParameters": {
                    "ids": ",".join(f"a{i}" for i in range(101))
                },
            },
            None,
        )
        assert response["statusCode"] == 400

    def test_extended_metadata_requires_an_asset_id(self, api):
        response = api.lambda_handler(
            {
                "httpMethod": "GET",
                "path": "/assets//extended-metadata",
                "pathParameters": {},
            },
            None,
        )
        assert response["statusCode"] == 400

    def test_cors_preflight_is_accepted(self, api):
        response = api.lambda_handler({"httpMethod": "OPTIONS", "path": "/"}, None)
        assert response["statusCode"] == 200

    def test_unknown_route_returns_not_found(self, api):
        response = api.lambda_handler(
            {"httpMethod": "GET", "path": "/nope"}, None
        )
        assert response["statusCode"] == 404

    def test_responses_carry_cors_headers(self, api):
        # The demo UI is served from a different origin than the API.
        response = api.lambda_handler({"httpMethod": "GET", "path": "/nope"}, None)
        assert "Access-Control-Allow-Origin" in response["headers"]


class TestCosineDistanceToSimilarity:
    """The API contract in api-spec.yaml documents ``score`` as a similarity
    in 0-1 where higher is better, but DynamoDB's COSINE SearchVectors returns
    a *distance* where lower is better. Passing the distance through inverted
    the ranking: the closest match was presented as the weakest, and sorting
    by relevance put the least similar asset first.
    """

    def test_converts_distance_to_similarity(self, search_handler):
        # Measured against the deployed index for the query 'wooden chair':
        # the wooden chair itself scored 0.384, a bed 0.849, a cabinet 0.895.
        f = search_handler.cosine_distance_to_similarity
        assert f(0.384) == pytest.approx(0.616)
        assert f(0.849) == pytest.approx(0.151)
        assert f(0.895) == pytest.approx(0.105)

    def test_preserves_ranking_order(self, search_handler):
        # Distances ascending (best first) must map to similarities
        # descending, so a descending sort by score ranks the best match first.
        f = search_handler.cosine_distance_to_similarity
        similarities = [f(d) for d in [0.384, 0.849, 0.895]]
        assert similarities == sorted(similarities, reverse=True)

    def test_identical_vectors_score_one(self, search_handler):
        assert search_handler.cosine_distance_to_similarity(0.0) == 1.0

    def test_clamps_opposing_vectors_to_zero(self, search_handler):
        # Cosine distance spans 0-2; past 1 the vectors point away from each
        # other, which is simply "no match". The documented range is 0-1, so
        # a negative score must not escape.
        f = search_handler.cosine_distance_to_similarity
        assert f(1.5) == 0.0
        assert f(2.0) == 0.0

    @pytest.mark.parametrize("value", [None, "", "not-a-number", {}])
    def test_returns_zero_for_unusable_values(self, search_handler, value):
        # A missing or malformed Score must not raise and abort the whole
        # search response.
        assert search_handler.cosine_distance_to_similarity(value) == 0.0


class TestBuildResult:
    """A browse result must omit `score` entirely rather than carry a
    placeholder. The UI decides whether a result is ranked by the presence of
    that field, and api-spec.yaml documents it as absent in browse mode.
    """

    ITEM = {
        "assetId": {"S": "asset-1"},
        "projectId": {"S": "project-1"},
        "title": {"S": "Modern Chair"},
        "category": {"S": "Furniture"},
        "primaryMaterial": {"S": "Wood"},
        "primaryColor": {"S": "Brown"},
        "tags": {"L": [{"S": "chair"}, {"S": "modern"}]},
    }

    def _stub_sdma(self, api, monkeypatch, asset=None, url=None):
        monkeypatch.setattr(api.sdma_client, "get_asset", lambda *a, **k: asset)
        monkeypatch.setattr(api.sdma_client, "get_thumbnail_url", lambda *a, **k: url)

    def test_omits_score_when_unranked(self, api, monkeypatch):
        self._stub_sdma(api, monkeypatch)
        assert "score" not in api.build_result(self.ITEM, None)

    def test_includes_score_when_ranked(self, api, monkeypatch):
        self._stub_sdma(api, monkeypatch)
        assert api.build_result(self.ITEM, 0.616)["score"] == 0.616

    def test_maps_metadata_and_tags(self, api, monkeypatch):
        self._stub_sdma(api, monkeypatch)

        result = api.build_result(self.ITEM, None)
        assert result["tags"] == ["chair", "modern"]
        assert result["structuredMetadata"]["category"] == "Furniture"
        assert result["structuredMetadata"]["materials"] == ["Wood"]
        assert result["structuredMetadata"]["primaryColors"] == ["Brown"]

    def test_prefers_the_sdma_asset_name_over_the_indexed_title(self, api, monkeypatch):
        self._stub_sdma(api, monkeypatch, asset={"assetName": "chair.glb"})

        assert api.build_result(self.ITEM, None)["assetName"] == "chair.glb"

    def test_passes_the_indexed_project_to_sdma(self, api, monkeypatch):
        # projectId is stored on the vector item precisely so search does not
        # have to resolve it: SDMA has no lookup from an asset id alone.
        seen = {}
        monkeypatch.setattr(api.sdma_client, "get_asset",
                            lambda aid, pid: seen.update(asset=aid, project=pid) or {})
        monkeypatch.setattr(api.sdma_client, "get_thumbnail_url", lambda *a, **k: None)

        api.build_result(self.ITEM, None)
        assert seen == {"asset": "asset-1", "project": "project-1"}

    def test_skips_sdma_when_the_item_predates_the_indexed_project(self, api, monkeypatch):
        # Items written before projectId was indexed cannot be resolved, and
        # must degrade to the indexed title rather than raising.
        monkeypatch.setattr(api.sdma_client, "get_asset",
                            lambda *a, **k: pytest.fail("must not call SDMA without a project"))
        monkeypatch.setattr(api.sdma_client, "get_thumbnail_url",
                            lambda *a, **k: pytest.fail("must not call SDMA without a project"))

        item = {k: v for k, v in self.ITEM.items() if k != "projectId"}
        result = api.build_result(item, None)
        assert result["assetName"] == "Modern Chair"
        assert result["thumbnailUrl"] is None


class TestEnrichItems:
    def test_preserves_input_order(self, api, monkeypatch):
        # Search ordering *is* the ranking, so the thread pool must not reorder.
        monkeypatch.setattr(api.sdma_client, "get_asset", lambda *a, **k: {})
        monkeypatch.setattr(api.sdma_client, "get_thumbnail_url", lambda *a, **k: None)

        items = [({"assetId": {"S": f"asset-{i}"}, "projectId": {"S": "p"}}, None)
                 for i in range(12)]
        results = api.enrich_items(items)
        assert [r["assetId"] for r in results] == [f"asset-{i}" for i in range(12)]

    def test_returns_empty_for_no_items(self, api):
        assert api.enrich_items([]) == []



class TestGetCategoryConfig:
    """The UI's filter dropdowns are populated from this. It must read the same
    vocabulary the AI tagger was given, or the UI offers filters that match
    nothing. It previously read a config/categories/default.json that nothing
    writes, so every call hit NoSuchKey and returned the all-empty fallback --
    leaving each dropdown with only its "All" entry.
    """

    TAGGING_YAML = """
version: '1.0'
filter_attributes:
  categories:
    Furniture:
      description: Seating and tables
      subcategories: [Chair, Table]
    Lighting:
      description: Light fixtures
      subcategories: [Floor Lamp]
  styles: [Modern, Traditional]
  materials: [Wood, Metal]
  colors: [Brown, White]
"""

    def _stub_s3(self, api, monkeypatch, body=None, error_code=None):
        class Body:
            def read(self_inner):
                return body.encode()

        def get_object(Bucket, Key):
            if error_code:
                raise ClientError({'Error': {'Code': error_code}}, 'GetObject')
            return {'Body': Body()}

        monkeypatch.setattr(api.s3_client, 'get_object', get_object)

    def test_reads_the_tagging_config(self, api, monkeypatch):
        self._stub_s3(api, monkeypatch, body=self.TAGGING_YAML)

        config = api.get_category_config()
        assert config['categories'] == {
            'Furniture': ['Chair', 'Table'],
            'Lighting': ['Floor Lamp'],
        }
        assert config['styles'] == ['Modern', 'Traditional']
        assert config['materials'] == ['Wood', 'Metal']
        assert config['colors'] == ['Brown', 'White']

    def test_reads_the_key_deploy_actually_uploads(self, api):
        # A path nothing writes silently degrades to the empty fallback.
        assert api.CONFIG_PATHS['tagging'] == 'config/tagging/default.yaml'

    def test_flattens_away_the_description_wrapper(self, api, monkeypatch):
        # The tagging config nests subcategories beside a description; the UI
        # wants a plain category -> [subcategory] mapping.
        self._stub_s3(api, monkeypatch, body=self.TAGGING_YAML)

        for subcats in api.get_category_config()['categories'].values():
            assert isinstance(subcats, list)

    def test_falls_back_when_the_config_is_absent(self, api, monkeypatch):
        self._stub_s3(api, monkeypatch, error_code='NoSuchKey')

        assert api.get_category_config() == api.DEFAULT_CATEGORY_CONFIG

    def test_falls_back_when_the_config_is_unparseable(self, api, monkeypatch):
        self._stub_s3(api, monkeypatch, body='key: [unclosed')

        assert api.get_category_config() == api.DEFAULT_CATEGORY_CONFIG

    def test_tolerates_a_config_without_filter_attributes(self, api, monkeypatch):
        self._stub_s3(api, monkeypatch, body="version: '1.0'\n")

        config = api.get_category_config()
        assert config == {'categories': {}, 'styles': [], 'materials': [], 'colors': []}

    def test_propagates_errors_other_than_a_missing_key(self, api, monkeypatch):
        self._stub_s3(api, monkeypatch, error_code='AccessDenied')

        with pytest.raises(ClientError):
            api.get_category_config()


class TestRequestBounds:
    """The API is Cognito-authorised, so these bound cost rather than block an
    anonymous attacker. Each result costs a DynamoDB read plus two SDMA calls,
    and the query is embedded by Bedrock and billed per token, so an unbounded
    value turns one authenticated request into an arbitrarily expensive one.
    api-spec.yaml documented these limits before the handler enforced them.
    """

    def _search(self, api, body):
        return api.lambda_handler(
            {"httpMethod": "POST", "path": "/assets/search",
             "body": json.dumps(body)}, None)

    def test_rejects_a_limit_above_the_documented_maximum(self, api):
        response = self._search(api, {"query": "desk", "limit": api.MAX_LIMIT + 1})
        assert response["statusCode"] == 400

    def test_rejects_a_limit_below_one(self, api):
        assert self._search(api, {"query": "desk", "limit": 0})["statusCode"] == 400

    def test_rejects_a_non_numeric_limit(self, api):
        # An unparsed value would reach DynamoDB and fail there instead.
        assert self._search(api, {"query": "desk", "limit": "all"})["statusCode"] == 400

    def test_accepts_the_documented_maximum(self, api, monkeypatch):
        monkeypatch.setattr(api, "search_assets", lambda q, f, k: [])
        assert self._search(
            api, {"query": "desk", "limit": api.MAX_LIMIT})["statusCode"] == 200

    def test_rejects_an_overlong_query(self, api):
        long_query = "x" * (api.MAX_QUERY_LENGTH + 1)
        assert self._search(api, {"query": long_query})["statusCode"] == 400

    def test_bounds_apply_to_browse_as_well(self, api):
        # Browse scans, and Limit is applied before FilterExpression, so a large
        # limit costs more pages -- not fewer.
        assert self._search(api, {"limit": api.MAX_LIMIT + 1})["statusCode"] == 400


class TestPostFilteredAttributes:
    """Only four attributes are INLINE_FILTERs on the index.

    Everything else api-spec.yaml documents -- projectId, subcategory,
    sizeCategory -- has to be applied to the results. Leaving them out of both
    places is what made them silently ignored on ranked search while working in
    browse, where Scan can filter on any attribute.
    """

    def _item(self, asset_id, **attrs):
        item = {"assetId": {"S": asset_id}, "category": {"S": "Furniture"}}
        item.update({k: {"S": v} for k, v in attrs.items()})
        return {"Item": item, "Score": 0.2}

    @pytest.mark.parametrize("key,value", [
        ("subcategory", "Chair"),
        ("sizeCategory", "large"),
    ])
    def test_keeps_only_the_matching_items(self, api, monkeypatch, key, value):
        monkeypatch.setattr(api._fake_ddb, "_results",
                            [self._item("a1", **{key: value}),
                             self._item("a2", **{key: "other"}),
                             self._item("a3", **{key: value})])
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.search_assets("desk", {key: value}, top_k=10)

        assert [r["assetId"]["S"] for r in results] == ["a1", "a3"]

    @pytest.mark.parametrize("key,value", [
        ("subcategory", "Chair"),
        ("sizeCategory", "large"),
    ])
    def test_over_fetches_so_a_filtered_page_is_not_short(self, api, key, value):
        api.search_assets("desk", {key: value}, top_k=10)
        params = api._fake_ddb.search_vectors_calls[0]

        assert params["TopK"] > 10

    def test_several_post_filters_all_apply(self, api, monkeypatch):
        monkeypatch.setattr(api._fake_ddb, "_results",
                            [self._item("a1", subcategory="Chair", sizeCategory="large"),
                             self._item("a2", subcategory="Chair", sizeCategory="small"),
                             self._item("a3", subcategory="Table", sizeCategory="large")])
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.search_assets(
            "desk", {"subcategory": "Chair", "sizeCategory": "large"}, top_k=10)

        assert [r["assetId"]["S"] for r in results] == ["a1"]


class TestBrowsePaging:
    """`Limit` is applied before `FilterExpression`, so browse pages.

    The loop that accumulates those pages used to be undone immediately after
    it: the accumulated list was reassigned from the last response, keeping only
    the final page. A filtered browse then returned fewer results than existed,
    or none, which is the exact failure the paging exists to prevent.
    """

    def _item(self, asset_id):
        return {"assetId": {"S": asset_id}, "category": {"S": "Furniture"}}

    def test_keeps_matches_from_every_page(self, api, monkeypatch):
        api._fake_ddb.scan_pages = [
            {"Items": [self._item("a1")], "LastEvaluatedKey": {"assetId": {"S": "a1"}}},
            {"Items": [self._item("a2")], "LastEvaluatedKey": {"assetId": {"S": "a2"}}},
            {"Items": [self._item("a3")]},
        ]
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.browse_assets({}, limit=10)

        assert sorted(r["assetId"]["S"] for r in results) == ["a1", "a2", "a3"]

    def test_a_page_that_matches_nothing_does_not_end_the_search(self, api, monkeypatch):
        # The first page is empty only because Limit was applied before the
        # filter. Treating that as "nothing left" is the trap.
        api._fake_ddb.scan_pages = [
            {"Items": [], "LastEvaluatedKey": {"assetId": {"S": "x"}}},
            {"Items": [self._item("a1")]},
        ]
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.browse_assets({"category": "Furniture"}, limit=10)

        assert [r["assetId"]["S"] for r in results] == ["a1"]

    def test_stops_at_the_requested_limit(self, api, monkeypatch):
        api._fake_ddb.scan_pages = [
            {"Items": [self._item(f"a{i}") for i in range(5)],
             "LastEvaluatedKey": {"assetId": {"S": "a4"}}},
            {"Items": [self._item(f"b{i}") for i in range(5)]},
        ]
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.browse_assets({}, limit=3)

        assert len(results) == 3


class TestStructuredMetadataShape:
    """Search and extended-metadata return one shape, from one builder.

    They used to disagree -- search returning `materials` and `primaryColors` as
    lists while extended-metadata returned scalar `primaryMaterial`,
    `primaryColor` and `secondaryColor` -- with api-spec.yaml describing both
    through a single schema.
    """

    def test_typed_and_unwrapped_items_produce_the_same_keys(self, api):
        typed = {"category": {"S": "Furniture"}, "primaryMaterial": {"S": "Wood"},
                 "primaryColor": {"S": "Brown"}, "secondaryColor": {"S": "Black"}}
        unwrapped = {"category": "Furniture", "primaryMaterial": "Wood",
                     "primaryColor": "Brown", "secondaryColor": "Black"}

        from_search = api.build_structured_metadata(
            lambda k: typed.get(k, {}).get("S", ""))
        from_extended = api.build_structured_metadata(
            lambda k: unwrapped.get(k, ""))

        assert from_search == from_extended
        assert from_search["materials"] == ["Wood"]
        assert from_search["primaryColors"] == ["Brown", "Black"]

    def test_absent_attributes_yield_empty_lists_not_empty_strings(self, api):
        built = api.build_structured_metadata(lambda k: "")

        assert built["materials"] == []
        assert built["primaryColors"] == []


class TestProjectIdFilter:
    """projectId is not one of the index's four INLINE_FILTER attributes.

    Putting it in SearchConditionExpression made SearchVectors fail with
    ValidationException, so a filter api-spec.yaml documents returned HTTP 500 on
    the search path while working in browse mode, where Scan can filter on any
    attribute. It is applied to the results instead.
    """

    def _item(self, asset_id, project_id):
        return {"Item": {"assetId": {"S": asset_id},
                         "projectId": {"S": project_id},
                         "category": {"S": "Furniture"}},
                "Score": 0.2}

    def test_is_not_sent_as_an_inline_condition(self, api):
        api.search_assets("desk", {"projectId": "project-1"})
        params = api._fake_ddb.search_vectors_calls[0]

        condition = params.get("SearchConditionExpression", "")
        assert "projectId" not in condition
        values = params.get("ExpressionAttributeValues") or {}
        assert ":projectId" not in values

    def test_other_filters_still_go_inline(self, api):
        api.search_assets("desk", {"projectId": "project-1", "category": "Furniture"})
        params = api._fake_ddb.search_vectors_calls[0]

        assert "category" in params["SearchConditionExpression"]

    def test_keeps_only_the_requested_project(self, api, monkeypatch):
        monkeypatch.setattr(api._fake_ddb, "_results",
                            [self._item("a1", "project-1"),
                             self._item("a2", "project-2"),
                             self._item("a3", "project-1")])
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.search_assets("desk", {"projectId": "project-1"}, top_k=10)

        assert [r["assetId"]["S"] for r in results] == ["a1", "a3"]

    def test_over_fetches_so_a_filtered_page_is_not_short(self, api):
        # Filtering happens after the search, so asking for exactly top_k would
        # return fewer than requested whenever any result is from another project.
        api.search_assets("desk", {"projectId": "project-1"}, top_k=10)
        params = api._fake_ddb.search_vectors_calls[0]

        assert params["TopK"] == 10 * api.OVER_FETCH_FACTOR

    def test_does_not_over_fetch_without_the_filter(self, api):
        api.search_assets("desk", {"category": "Furniture"}, top_k=10)

        assert api._fake_ddb.search_vectors_calls[0]["TopK"] == 10

    def test_over_fetch_is_capped(self, api):
        # A limit of 100 must not become an unbounded read.
        api.search_assets("desk", {"projectId": "project-1"}, top_k=api.MAX_LIMIT)

        assert api._fake_ddb.search_vectors_calls[0]["TopK"] == api.MAX_TOP_K

    def test_never_returns_more_than_asked_for(self, api, monkeypatch):
        monkeypatch.setattr(api._fake_ddb, "_results",
                            [self._item(f"a{i}", "project-1") for i in range(20)])
        monkeypatch.setattr(api, "enrich_items", lambda pairs: [p[0] for p in pairs])

        results = api.search_assets("desk", {"projectId": "project-1"}, top_k=5)

        assert len(results) == 5
