from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import structlog

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.tickets import (
    ApproveTicketInput,
    CreateTicketInput,
    GetTicketDetailInput,
    ListMyTicketsInput,
)

log = structlog.get_logger(__name__)

_SUPPORTED_REQUEST_TYPE_CODES = {
    "work_from_home",
    "late_coming",
    "early_leave",
    "paid_leave",
    "overtime_request",
    "others",
}

_REQUEST_TYPE_LABELS = {
    "work_from_home": "Work from home",
    "late_coming": "Late coming",
    "early_leave": "Early leave",
    "paid_leave": "Paid leave",
    "overtime_request": "Overtime request",
    "others": "General request",
}

_REQUEST_TYPE_ALIASES = {
    "wfh": "work_from_home",
    "remote_work": "work_from_home",
    "remote": "work_from_home",
    "late": "late_coming",
    "late_coming_request": "late_coming",
    "early_leave_request": "early_leave",
    "leave": "paid_leave",
    "leave_request": "paid_leave",
    "annual_leave": "paid_leave",
    "paid_leave_last_year": "paid_leave",
    "sick_leave": "paid_leave",
    "medical_leave": "paid_leave",
    "sick_leave_with_hospital_paper": "paid_leave",
    "vacation_leave": "paid_leave",
    "day_off": "paid_leave",
    "ot": "overtime_request",
    "overtime": "overtime_request",
    "overtime_standard": "overtime_request",
}

_SICK_REQUEST_ALIASES = {
    "sick_leave",
    "medical_leave",
    "sick_leave_with_hospital_paper",
}


@dataclass(frozen=True)
class NormalizedCreateTicketPayload:
    request_type_code: str
    request_type_name: Optional[str]
    title: str
    content: str
    reason_code: Optional[str]
    reason_detail: Optional[str]
    delegate_id: Optional[str]
    effort_owner_id: Optional[str]
    cc_member_ids: Optional[list[str]]
    start_at: Optional[str]
    end_at: Optional[str]


class TicketExecutor:
    @staticmethod
    async def list_my_tickets(input: ListMyTicketsInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.list_my_tickets(
            status=input.status,
            page=input.page,
            limit=input.limit,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def get_ticket_detail(input: GetTicketDetailInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_ticket_detail(
            ticket_id=input.ticket_id,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def create_ticket(input: CreateTicketInput, ctx: ToolContext) -> dict[str, Any]:
        normalized = TicketExecutor._normalize_create_ticket_input(input)
        log.info(
            "ticket_executor.create_ticket_normalized",
            trace_id=ctx.trace_id,
            original_request_type=input.request_type_code or input.request_type_id,
            request_type_code=normalized.request_type_code,
            reason_code=normalized.reason_code,
            start_at=normalized.start_at,
            end_at=normalized.end_at,
        )
        return await BeCoreClient.create_ticket(
            request_type_code=normalized.request_type_code,
            request_type_name=normalized.request_type_name,
            title=normalized.title,
            content=normalized.content,
            reason_code=normalized.reason_code,
            reason_detail=normalized.reason_detail,
            delegate_id=normalized.delegate_id,
            effort_owner_id=normalized.effort_owner_id,
            cc_member_ids=normalized.cc_member_ids,
            start_at=normalized.start_at,
            end_at=normalized.end_at,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def approve_ticket(input: ApproveTicketInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.approve_ticket(
            ticket_id=input.ticket_id,
            comment=input.comment,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    def _normalize_create_ticket_input(
        input: CreateTicketInput,
    ) -> NormalizedCreateTicketPayload:
        raw_request_type = TicketExecutor._clean_text(
            input.request_type_code or input.request_type_id
        )
        request_type_code, request_type_name = TicketExecutor._normalize_request_type(
            raw_request_type=raw_request_type,
            request_type_name=TicketExecutor._clean_text(input.request_type_name),
        )

        reason = TicketExecutor._clean_text(input.reason)
        notes = TicketExecutor._clean_text(input.notes)
        reason_detail = TicketExecutor._clean_text(input.reason_detail)
        content = TicketExecutor._clean_text(input.content)
        title = TicketExecutor._clean_text(input.title)

        inferred_reason_code = TicketExecutor._infer_reason_code(
            explicit_reason_code=TicketExecutor._clean_text(input.reason_code),
            request_type_code=request_type_code,
            raw_request_type=raw_request_type,
            reason=reason,
            notes=notes,
            content=content,
            reason_detail=reason_detail,
        )

        normalized_title = title or TicketExecutor._build_title(
            request_type_code=request_type_code,
            raw_request_type=raw_request_type,
            reason_code=inferred_reason_code,
            start_date=input.start_date,
            end_date=input.end_date,
            start_at=input.start_at,
            end_at=input.end_at,
        )
        normalized_content = content or TicketExecutor._build_content(
            reason=reason,
            notes=notes,
            reason_detail=reason_detail,
        )
        normalized_reason_detail = reason_detail or TicketExecutor._derive_reason_detail(
            reason=reason,
            notes=notes,
            content=content,
        )
        start_at, end_at = TicketExecutor._normalize_datetime_range(
            start_date=input.start_date,
            end_date=input.end_date,
            start_at=input.start_at,
            end_at=input.end_at,
        )

        return NormalizedCreateTicketPayload(
            request_type_code=request_type_code,
            request_type_name=request_type_name,
            title=normalized_title,
            content=normalized_content,
            reason_code=inferred_reason_code,
            reason_detail=normalized_reason_detail,
            delegate_id=TicketExecutor._clean_text(input.delegate_id),
            effort_owner_id=TicketExecutor._clean_text(input.effort_owner_id),
            cc_member_ids=input.cc_member_ids,
            start_at=start_at,
            end_at=end_at,
        )

    @staticmethod
    def _normalize_request_type(
        raw_request_type: Optional[str],
        request_type_name: Optional[str],
    ) -> tuple[str, Optional[str]]:
        normalized = (raw_request_type or "").strip().lower()
        normalized = _REQUEST_TYPE_ALIASES.get(normalized, normalized)

        if normalized in _SUPPORTED_REQUEST_TYPE_CODES:
            resolved_name = request_type_name
            if normalized == "others" and not resolved_name:
                resolved_name = "General request"
            return normalized, resolved_name

        fallback_name = request_type_name or TicketExecutor._humanize_code(
            raw_request_type or "general_request"
        )
        return "others", fallback_name

    @staticmethod
    def _infer_reason_code(
        explicit_reason_code: Optional[str],
        request_type_code: str,
        raw_request_type: Optional[str],
        reason: Optional[str],
        notes: Optional[str],
        content: Optional[str],
        reason_detail: Optional[str],
    ) -> Optional[str]:
        if explicit_reason_code:
            return explicit_reason_code

        combined_text = " ".join(
            value for value in [reason, notes, content, reason_detail] if value
        ).lower()
        raw_code = (raw_request_type or "").strip().lower()

        if raw_code in _SICK_REQUEST_ALIASES:
            return "medical_reason"
        if any(keyword in combined_text for keyword in ("dieu tri", "điều trị", "treatment")):
            return "medical_treatment"
        if any(
            keyword in combined_text
            for keyword in ("sick", "ill", "benh", "bệnh", "om", "ốm", "sot", "sốt", "medical")
        ):
            return "medical_reason"
        if any(keyword in combined_text for keyword in ("gia dinh", "family")):
            return "family_reason"
        if any(
            keyword in combined_text
            for keyword in ("annual leave", "phep nam", "phép năm", "vacation")
        ):
            return "annual_leave"
        if request_type_code == "overtime_request":
            return "ot_other"

        return None

    @staticmethod
    def _build_title(
        request_type_code: str,
        raw_request_type: Optional[str],
        reason_code: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        start_at: Optional[str],
        end_at: Optional[str],
    ) -> str:
        if reason_code in {"medical_reason", "medical_treatment"}:
            base = "Sick leave request"
        else:
            base = _REQUEST_TYPE_LABELS.get(
                request_type_code,
                TicketExecutor._humanize_code(raw_request_type or request_type_code),
            )

        date_label = TicketExecutor._build_date_label(
            start_date=start_date,
            end_date=end_date,
            start_at=start_at,
            end_at=end_at,
        )
        if date_label:
            return TicketExecutor._truncate_text(f"{base} ({date_label})", limit=255)
        return TicketExecutor._truncate_text(base, limit=255)

    @staticmethod
    def _build_content(
        reason: Optional[str],
        notes: Optional[str],
        reason_detail: Optional[str],
    ) -> str:
        parts: list[str] = []
        for value in (reason, reason_detail, notes):
            cleaned = TicketExecutor._clean_text(value)
            if cleaned and cleaned not in parts:
                parts.append(cleaned)

        if not parts:
            parts.append("Ticket request created from chatbot.")

        return "\n\n".join(parts)

    @staticmethod
    def _derive_reason_detail(
        reason: Optional[str],
        notes: Optional[str],
        content: Optional[str],
    ) -> Optional[str]:
        candidates = [
            TicketExecutor._clean_text(notes),
            TicketExecutor._clean_text(reason),
            TicketExecutor._clean_text(content),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        return None

    @staticmethod
    def _normalize_datetime_range(
        start_date: Optional[str],
        end_date: Optional[str],
        start_at: Optional[str],
        end_at: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        normalized_start_at = TicketExecutor._clean_text(start_at)
        normalized_end_at = TicketExecutor._clean_text(end_at)

        if normalized_start_at or normalized_end_at:
            return normalized_start_at, normalized_end_at

        resolved_start_date = TicketExecutor._clean_text(start_date)
        resolved_end_date = TicketExecutor._clean_text(end_date) or resolved_start_date
        if not resolved_start_date and resolved_end_date:
            resolved_start_date = resolved_end_date

        if not resolved_start_date:
            return None, None

        start_value = date.fromisoformat(resolved_start_date)
        end_value = date.fromisoformat(resolved_end_date or resolved_start_date)

        return (
            f"{start_value.isoformat()}T00:00:00.000Z",
            f"{end_value.isoformat()}T23:59:59.999Z",
        )

    @staticmethod
    def _build_date_label(
        start_date: Optional[str],
        end_date: Optional[str],
        start_at: Optional[str],
        end_at: Optional[str],
    ) -> Optional[str]:
        resolved_start = TicketExecutor._clean_text(start_date)
        resolved_end = TicketExecutor._clean_text(end_date)
        normalized_start_at = TicketExecutor._clean_text(start_at)
        normalized_end_at = TicketExecutor._clean_text(end_at)

        if not resolved_start and normalized_start_at:
            resolved_start = normalized_start_at[:10]
        if not resolved_end and normalized_end_at:
            resolved_end = normalized_end_at[:10]
        if not resolved_end:
            resolved_end = resolved_start

        if not resolved_start:
            return None
        if resolved_start == resolved_end:
            return resolved_start
        return f"{resolved_start} to {resolved_end}"

    @staticmethod
    def _humanize_code(value: str) -> str:
        return value.replace("_", " ").strip().title()

    @staticmethod
    def _truncate_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    @staticmethod
    def _clean_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
