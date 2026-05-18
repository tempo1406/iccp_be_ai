from __future__ import annotations

from typing import Any

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.documents import GetDocumentTreeInput, ListDocumentsInput


class DocumentExecutor:
    @staticmethod
    async def list_documents(input: ListDocumentsInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.list_documents(
            folder_id=input.folder_id,
            category_id=input.category_id,
            project_id=input.project_id,
            page=input.page,
            limit=input.limit,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def get_document_tree(input: GetDocumentTreeInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_document_tree(
            include_deleted=input.include_deleted or False,
            bearer_token=ctx.bearer_token,
        )
