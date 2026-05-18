from __future__ import annotations

from dataclasses import dataclass

import structlog

from .registry import READ_TOOLS, WRITE_TOOLS

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SafeguardResult:
    """Result of safeguard check."""

    allowed: bool
    action_type: str
    requires_confirmation: bool
    preview: str = ""
    reason: str = ""


class SafeguardGate:
    """
    Determines whether a tool call requires user confirmation.

    - READ operations: allowed immediately
    - WRITE operations: require explicit user confirmation
    """

    @classmethod
    def check(cls, tool_name: str, params: dict) -> SafeguardResult:
        if tool_name in READ_TOOLS:
            return SafeguardResult(
                allowed=True,
                action_type="read",
                requires_confirmation=False,
                preview=cls._build_preview(tool_name, params),
            )

        if tool_name in WRITE_TOOLS:
            preview = cls._build_preview(tool_name, params)
            return SafeguardResult(
                allowed=True,
                action_type="write",
                requires_confirmation=True,
                preview=preview,
                reason="Write operations require user confirmation",
            )

        log.warning("safeguard.unknown_tool", tool_name=tool_name)
        return SafeguardResult(
            allowed=False,
            action_type="unknown",
            requires_confirmation=False,
            reason=f"Unknown tool: {tool_name}",
        )

    @staticmethod
    def _build_preview(tool_name: str, params: dict) -> str:
        """Build a human-readable preview of the action for UI display."""
        date_str = params.get("date", "")
        project_id = params.get("project_id", "")
        task_id = params.get("task_id", "")
        status_id = params.get("status_id", "")
        report_id = params.get("report_id", "")
        ticket_id = params.get("ticket_id", "")
        reason = params.get("reason", "")
        title = params.get("title", "")
        request_type_code = params.get("request_type_code", "") or params.get(
            "request_type_id", ""
        )

        if tool_name == "list_tasks":
            return f"Tìm task trong dự án {project_id}" if project_id else "Tìm task của bạn"
        if tool_name == "get_task_detail":
            return f"Xem chi tiết task {task_id}"
        if tool_name == "create_task":
            return f"Tạo task mới: {title}"
        if tool_name == "update_task_status":
            return f"Cập nhật trạng thái task {task_id} thành {status_id}"
        if tool_name == "add_task_comment":
            return f"Thêm bình luận vào task {task_id}"
        if tool_name == "list_projects":
            return "Liệt kê danh sách dự án"
        if tool_name == "get_project_detail":
            return f"Xem chi tiết dự án {project_id}"
        if tool_name == "get_daily_report":
            return f"Xem daily report ngày {date_str}" if date_str else "Xem daily report hôm nay"
        if tool_name == "submit_daily_report":
            return f"Submit daily report {report_id}"
        if tool_name == "list_my_tickets":
            return "Liệt kê ticket của bạn"
        if tool_name == "get_ticket_detail":
            return f"Xem chi tiết ticket {ticket_id}"
        if tool_name == "create_ticket":
            preview = title or reason[:40] or request_type_code
            return f"Tạo ticket mới: {preview}" if preview else "Tạo ticket mới"
        if tool_name == "approve_ticket":
            return f"Duyệt ticket {ticket_id}"
        if tool_name == "list_documents":
            return "Liệt kê tài liệu"
        if tool_name == "get_document_tree":
            return "Xem cây thư mục tài liệu"
        if tool_name == "get_org_profile":
            return "Xem thông tin tổ chức"
        if tool_name == "list_org_members":
            return "Liệt kê thành viên tổ chức"

        return f"Thực hiện: {tool_name}"
