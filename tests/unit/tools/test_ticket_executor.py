from app.tools.executors.tickets import TicketExecutor
from app.tools.schemas.tickets import CreateTicketInput


def test_create_ticket_normalizes_legacy_sick_leave_payload():
    payload = TicketExecutor._normalize_create_ticket_input(
        CreateTicketInput(
            request_type_id="sick_leave",
            reason="Toi bi sot va can nghi de theo doi suc khoe.",
            start_date="2026-04-25",
            end_date="2026-04-25",
            notes="Mong cap tren xem xet va phe duyet.",
        )
    )

    assert payload.request_type_code == "paid_leave"
    assert payload.reason_code == "medical_reason"
    assert payload.title == "Sick leave request (2026-04-25)"
    assert payload.content == (
        "Toi bi sot va can nghi de theo doi suc khoe.\n\n"
        "Mong cap tren xem xet va phe duyet."
    )
    assert payload.reason_detail == "Mong cap tren xem xet va phe duyet."
    assert payload.start_at == "2026-04-25T00:00:00.000Z"
    assert payload.end_at == "2026-04-25T23:59:59.999Z"


def test_create_ticket_unknown_request_type_falls_back_to_others():
    payload = TicketExecutor._normalize_create_ticket_input(
        CreateTicketInput(
            request_type_id="custom_special_case",
            reason="Can xu ly mot truong hop ngoai le.",
        )
    )

    assert payload.request_type_code == "others"
    assert payload.request_type_name == "Custom Special Case"
    assert payload.title == "General request"
    assert payload.content == "Can xu ly mot truong hop ngoai le."


def test_create_ticket_schema_keeps_cc_member_ids_as_string_array():
    schema = CreateTicketInput.model_json_schema()
    cc_member_ids = schema["properties"]["cc_member_ids"]

    assert cc_member_ids["type"] == "array"
    assert cc_member_ids["items"]["type"] == "string"
