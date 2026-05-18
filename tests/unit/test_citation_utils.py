from app.agents.citation_utils import (
    CitationCandidate,
    CitationPreview,
    build_display_citations,
    collapse_citations,
    collapse_stored_citations,
)
from app.db.schemas.message import CitationSchema


def test_collapse_citations_groups_chunks_by_document():
    collapsed = collapse_citations(
        [
            CitationPreview(
                document_id="doc-1",
                chunk_index=4,
                file_name="report-6.docx",
                relevance_score=0.12,
                cited_content="Lower-ranked chunk.",
            ),
            CitationPreview(
                document_id="doc-1",
                chunk_index=1,
                file_name="report-6.docx",
                relevance_score=0.31,
                cited_content="Best chunk.",
            ),
            CitationPreview(
                document_id="doc-2",
                chunk_index=0,
                file_name="policy.pdf",
                relevance_score=0.27,
                cited_content="Different source.",
            ),
        ]
    )

    assert len(collapsed) == 2
    assert collapsed[0].document_id == "doc-1"
    assert collapsed[0].chunk_index == 1
    assert collapsed[0].relevance_score == 0.31
    assert collapsed[0].chunk_count == 2
    assert collapsed[1].document_id == "doc-2"
    assert collapsed[1].chunk_count == 1


def test_collapse_stored_citations_preserves_best_chunk_metadata():
    collapsed = collapse_stored_citations(
        [
            CitationSchema(
                document_id="doc-1",
                document_name="report-6.docx",
                chunk_text="Chunk cu hon",
                score=0.18,
            ),
            CitationSchema(
                document_id="doc-1",
                document_name="report-6.docx",
                chunk_text="Chunk tot nhat",
                score=0.42,
                chunk_index=7,
            ),
        ]
    )

    assert len(collapsed) == 1
    assert collapsed[0].document_id == "doc-1"
    assert collapsed[0].file_name == "report-6.docx"
    assert collapsed[0].chunk_index == 7
    assert collapsed[0].relevance_score == 0.42
    assert collapsed[0].chunk_count == 2


def test_build_display_citations_prioritizes_query_matching_document_and_filters_noise():
    display = build_display_citations(
        [
            CitationCandidate(
                document_id="privacy-doc",
                chunk_index=1,
                file_name="1__PRIVACY_POLICY__VI__Chinh_sach_quyen_rieng_tu.pdf",
                raw_score=0.0314,
                cited_content="Privacy policy chunk.",
                vector_score=0.84,
                lexical_score=3.2,
            ),
            CitationCandidate(
                document_id="report-6",
                chunk_index=0,
                file_name="Report6_Software_User_Guides_TT7_PFqo5.docx",
                raw_score=0.0314,
                cited_content="WeatherTrip user guide chunk.",
                vector_score=0.84,
            ),
        ],
        query="Policy trong tai lieu Quyen rieng tu Privacy Policy la gi",
    )

    assert len(display) == 1
    assert display[0].document_id == "privacy-doc"
    assert display[0].file_name.startswith("1__PRIVACY_POLICY")
    assert 0.0 <= display[0].relevance_score <= 1.0


def test_build_display_citations_uses_chunk_support_for_single_document():
    display = build_display_citations(
        [
            CitationCandidate(
                document_id="privacy-doc",
                chunk_index=0,
                file_name="privacy-policy.pdf",
                cited_content="Chinh sach thu thap du lieu ca nhan va luu tru du lieu.",
                raw_score=0.032,
                vector_score=0.81,
                lexical_score=4.1,
            ),
            CitationCandidate(
                document_id="privacy-doc",
                chunk_index=4,
                file_name="privacy-policy.pdf",
                cited_content="Nguoi dung co quyen truy cap, sua va xoa du lieu.",
                raw_score=0.029,
                vector_score=0.79,
                lexical_score=3.8,
            ),
        ],
        query="quyen xoa du lieu trong privacy policy",
    )

    assert len(display) == 1
    assert display[0].document_id == "privacy-doc"
    assert display[0].chunk_count == 2
    assert display[0].chunk_index in {0, 4}


def test_build_display_citations_filters_secondary_doc_with_weak_relative_match():
    display = build_display_citations(
        [
            CitationCandidate(
                document_id="privacy-doc",
                chunk_index=0,
                file_name="privacy-policy.pdf",
                cited_content="Privacy policy defines data collection and processing terms.",
                raw_score=0.95,
                vector_score=0.91,
                lexical_score=6.2,
            ),
            CitationCandidate(
                document_id="report-6",
                chunk_index=2,
                file_name="Report6_Software_User_Guides.docx",
                cited_content="Software user guide with onboarding and dashboard walkthrough.",
                raw_score=0.42,
                vector_score=0.29,
                lexical_score=2.5,
            ),
        ],
        query="privacy policy du lieu ca nhan la gi",
    )

    assert len(display) == 1
    assert display[0].document_id == "privacy-doc"


def test_collapse_stored_citations_preserves_existing_chunk_count():
    collapsed = collapse_stored_citations(
        [
            CitationSchema(
                document_id="privacy-doc",
                document_name="privacy-policy.pdf",
                chunk_text="Representative chunk",
                score=0.92,
                chunk_index=3,
                chunk_count=4,
            )
        ]
    )

    assert len(collapsed) == 1
    assert collapsed[0].chunk_count == 4
