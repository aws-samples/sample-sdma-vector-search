# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for correcting AI metadata against the configured vocabulary.

`config/tagging/default.yaml` -> `filter_attributes` is both the tagger's
vocabulary and the source of the UI's filter dropdowns, and four of its fields
are the vector index's INLINE_FILTER attributes. A value outside the vocabulary
therefore makes an asset unreachable by that filter -- silently, because the
write succeeds and the asset still appears in unfiltered search.

That is not hypothetical: a run of 140 assets produced `Cardboard`, `Paper`,
`Drywall` and `Teal`, none of which the vocabulary offers, because material and
colour were the two inline filter attributes this function did not check.
"""
import pytest

from conftest import load_function_module


@pytest.fixture
def cfg_module():
    return load_function_module("ai-tag-generation", "category_config")


@pytest.fixture
def config():
    """A vocabulary shaped like the normalised form the loader produces."""
    return {
        'categories': {'Furniture': {'subcategories': ['Chair', 'Table']}},
        'styles': ['Modern', 'Traditional'],
        'materials': ['Wood', 'Metal'],
        'colors': ['Brown', 'Black'],
        'fallback_category': 'Prop',
        'fallback_subcategory': 'Other',
    }


def _validate(cfg_module, config, **structured):
    result = cfg_module.validate_metadata_against_config(
        {'structuredMetadata': structured}, config)
    return result['structuredMetadata']


class TestInlineFilterAttributes:
    """All four must be corrected: each one is an INLINE_FILTER column.

    Material and colour arrive as *lists* (`materials`, `primaryColors`) and
    vector_indexer.py indexes the first element of each, so the lists are what
    must be corrected. Correcting a scalar `primaryMaterial` instead would be
    silently ignored -- the indexer never reads it.
    """

    def test_drops_an_unlisted_material(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        subcategory='Chair', style='Modern',
                        materials=['Cardboard', 'Wood'],
                        primaryColors=['Brown'])
        # Wood was listed and stays first, so the indexed value is in vocabulary.
        assert out['materials'] == ['Wood']

    def test_drops_an_unlisted_colour(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        materials=['Wood'], primaryColors=['Teal', 'Black'])
        assert out['primaryColors'] == ['Black']

    def test_falls_back_when_nothing_is_listed(self, cfg_module, config):
        # An empty list would leave the inline filter attribute blank, which makes
        # the asset unfilterable -- worse than an approximate value.
        out = _validate(cfg_module, config, category='Furniture',
                        materials=['Cardboard', 'Paper'],
                        primaryColors=['Teal'])
        assert out['materials'] == ['Wood']
        assert out['primaryColors'] == ['Brown']

    def test_keeps_the_order_the_model_chose(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        materials=['Metal', 'Wood'], primaryColors=['Black'])
        assert out['materials'] == ['Metal', 'Wood']

    def test_corrects_an_unlisted_category(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Vehicle')
        assert out['category'] == 'Prop'

    def test_corrects_an_unlisted_style(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        subcategory='Chair', style='Brutalist')
        assert out['style'] == 'Modern'

    def test_corrects_an_unlisted_subcategory(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        subcategory='Hammock')
        assert out['subcategory'] == 'Other'

    def test_leaves_listed_values_alone(self, cfg_module, config):
        out = _validate(cfg_module, config, category='Furniture',
                        subcategory='Table', style='Traditional',
                        materials=['Metal'], primaryColors=['Black'])
        assert out['materials'] == ['Metal']
        assert out['primaryColors'] == ['Black']
        assert out['style'] == 'Traditional'
        assert out['subcategory'] == 'Table'


class TestAllowUnlisted:
    def test_allow_unlisted_keeps_the_model_output(self, cfg_module, config):
        # An operator who opts in accepts that those assets are unreachable by
        # the dropdowns, which only ever offer the configured values.
        config['allow_unlisted'] = True
        out = _validate(cfg_module, config, category='Furniture',
                        subcategory='Chair', style='Modern',
                        materials=['Cardboard'], primaryColors=['Teal'])
        assert out['materials'] == ['Cardboard']
        assert out['primaryColors'] == ['Teal']


class TestEmptyVocabulary:
    def test_an_absent_list_is_not_enforced(self, cfg_module):
        # With nothing configured there is no vocabulary to correct against, and
        # replacing the value with a guess would be worse than leaving it.
        out = _validate(cfg_module, {'categories': {}},
                        category='Furniture', materials=['Cardboard'])
        assert out['materials'] == ['Cardboard']

    def test_a_non_list_value_is_left_alone(self, cfg_module, config):
        # The indexer reads lists. A scalar means the model returned an unexpected
        # shape, and guessing a correction could mask that.
        out = _validate(cfg_module, config, category='Furniture',
                        materials='Cardboard')
        assert out['materials'] == 'Cardboard'
