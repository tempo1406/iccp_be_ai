from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ListMyTicketsInput(BaseModel):
    """List tickets created by or assigned to the user."""

    status: Optional[str] = Field(None, description="Filter by status")
    page: Optional[int] = Field(1, description="Page number")
    limit: Optional[int] = Field(20, description="Items per page")


class GetTicketDetailInput(BaseModel):
    """Get ticket details."""

    ticket_id: str = Field(..., description="Ticket ID")


class CreateTicketInput(BaseModel):
    """Create a new ticket request."""

    request_type_id: Optional[str] = Field(
        None,
        description=(
            "Legacy request type alias from chatbot flows, for example: "
            "sick_leave, annual_leave, wfh, overtime."
        ),
    )
    request_type_code: Optional[str] = Field(
        None,
        description=(
            "be_core request type code, for example: paid_leave, work_from_home, "
            "late_coming, early_leave, overtime_request, others."
        ),
    )
    request_type_name: Optional[str] = Field(
        None,
        description="Custom request type name when request_type_code is others.",
    )
    title: Optional[str] = Field(
        None,
        description="Short ticket title. If omitted, executor will generate one.",
    )
    content: Optional[str] = Field(
        None,
        description="Detailed ticket content/body. If omitted, executor will generate one.",
    )
    reason: Optional[str] = Field(
        None,
        description="Human-readable reason for the request.",
    )
    reason_code: Optional[str] = Field(
        None,
        description=(
            "Optional be_core reason code, for example: medical_reason, "
            "medical_treatment, annual_leave, family_reason, other."
        ),
    )
    reason_detail: Optional[str] = Field(
        None,
        description="Optional detailed reason text sent to be_core.",
    )
    start_date: Optional[str] = Field(
        None,
        description="Legacy start date in YYYY-MM-DD format.",
    )
    end_date: Optional[str] = Field(
        None,
        description="Legacy end date in YYYY-MM-DD format.",
    )
    start_at: Optional[str] = Field(
        None,
        description="Start datetime in ISO-8601 format expected by be_core.",
    )
    end_at: Optional[str] = Field(
        None,
        description="End datetime in ISO-8601 format expected by be_core.",
    )
    delegate_id: Optional[str] = Field(None, description="Delegate user ID")
    effort_owner_id: Optional[str] = Field(None, description="Effort owner user ID")
    cc_member_ids: list[str] = Field(
        default_factory=list,
        description="CC member user IDs",
    )
    notes: Optional[str] = Field(None, description="Additional notes")

    @model_validator(mode="after")
    def validate_ticket_request(self) -> "CreateTicketInput":
        if not self.request_type_code and not self.request_type_id:
            raise ValueError("request_type_code or request_type_id is required")

        if not any(
            value and str(value).strip()
            for value in (
                self.reason,
                self.content,
                self.notes,
                self.reason_detail,
            )
        ):
            raise ValueError(
                "At least one of reason, content, notes, or reason_detail is required"
            )

        return self


class ApproveTicketInput(BaseModel):
    """Approve a ticket workflow step."""

    ticket_id: str = Field(..., description="Ticket ID to approve")
    comment: Optional[str] = Field(None, description="Approval comment")
