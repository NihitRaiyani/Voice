from uuid import uuid4

import pytest


def test_cosine_teaching_example_and_invalid_vectors():
    from roma.domain.knowledge.retrieval import cosine_similarity
    assert cosine_similarity((1, 0), (1, 0)) == pytest.approx(1)
    assert cosine_similarity((1, 0), (0, 1)) == pytest.approx(0)
    assert cosine_similarity((1, 0), (-1, 0)) == pytest.approx(-1)
    for left, right in [((0, 0), (1, 0)), ((1,), (1, 0)), ((float('nan'),), (1,))]:
        with pytest.raises(ValueError):
            cosine_similarity(left, right)


def test_filters_keep_english_fallback_explicit_and_scope_typed():
    from roma.domain.knowledge.retrieval import RetrievalFilters
    course = uuid4()
    assert RetrievalFilters('course', 'gu', course_id=course).languages == ('gu', 'en')
    assert RetrievalFilters('course', 'en').languages == ('en',)
    assert RetrievalFilters('policy', 'gu', allow_english=False).languages == ('gu',)
    with pytest.raises(ValueError):
        RetrievalFilters('fees', 'gu')
    with pytest.raises(ValueError):
        RetrievalFilters('course', 'gu', course_id='untrusted')
