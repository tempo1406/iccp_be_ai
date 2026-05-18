# 06 — Web: Trigger Chunking + Document-Scoped Chat

> **Repo:** `iccp_web`
> **Prerequisite:** File 01–05 (backend phải hoàn chỉnh trước)
> **Files sửa/tạo:** document-detail, chatbot components, hooks

---

## Mục tiêu

1. **Trigger chunking button** — wire nút "Trigger chunking" trong document detail page gọi đúng API
2. **Real-time status update** — document status update qua WebSocket khi indexing xong
3. **Document-scoped chat** — nút "Chat về tài liệu này" + scope selector trong chatbot
4. **Citation click-through** — click vào citation mở document detail

---

## Phần A — Trigger Chunking

### A1 — Thêm mutation `useTriggerChunking`

**File:** `src/features/tenant/documents/query/use-documents.ts`

```typescript
// Thêm vào file này

export const INGEST_JOB_QUERY_KEYS = {
  status: (docId: string) => ['ingest', 'status', docId] as const,
};

// Service call — gọi be_ai trực tiếp
export function useTriggerChunking() {
  const ctx = useServiceContext();
  const queryClient = useQueryClient();

  return useSafeMutation(
    useMutation({
      mutationFn: async (documentId: string) => {
        return new AiService(ctx).triggerIngest(documentId);
      },
      onSuccess: (_, documentId) => {
        // Invalidate document detail để refetch status
        queryClient.invalidateQueries({
          queryKey: ['documents', ctx.tenantId, 'detail', documentId],
        });
        toast.success('Đã gửi yêu cầu xử lý tài liệu');
      },
      onError: () => {
        toast.error('Không thể kích hoạt xử lý tài liệu');
      },
    }),
  );
}
```

### A2 — Tạo `AiService`

**File:** `src/services/ai/ai.service.ts`

```typescript
import { BaseService } from '@/services/base-service';

export interface IngestJobResponse {
  jobId: string;
  status: 'queued' | 'processing' | 'indexed' | 'failed';
}

export class AiService extends BaseService {
  // Override base URL — gọi AI service không phải core
  protected override get baseUrl(): string {
    return appConfig.aiBaseUrl; // NEXT_PUBLIC_AI_BASE_URL
  }

  async triggerIngest(documentId: string): Promise<IngestJobResponse> {
    return this.post('/api/v1/ingest/documents', {
      document_id: documentId,
      organization_id: this.ctx.tenantId,
    });
  }

  async getIngestJobStatus(jobId: string): Promise<IngestJobResponse> {
    return this.get(`/api/v1/ingest/jobs/${jobId}`);
  }
}
```

### A3 — Sửa `document-detail-page.tsx`

Tìm phần "Trigger chunking" và thay toast bằng mutation thực:

```typescript
// Thay thế đoạn stub hiện tại
const { mutate: triggerChunking, isPending: isTriggering } = useTriggerChunking();

// Trong component, điều kiện hiện nút:
// Chỉ hiện khi status là not_indexed, pending, hoặc failed
const canTrigger = ['not_indexed', 'pending', 'failed'].includes(document?.status ?? '');

// Button:
{canTrigger && (
  <Button
    onClick={() => triggerChunking(document.id)}
    disabled={isTriggering || document.status === 'processing'}
    variant="outline"
    size="sm"
  >
    {isTriggering || document.status === 'processing'
      ? 'Đang xử lý...'
      : 'Kích hoạt AI Index'}
  </Button>
)}
```

### A4 — Real-time status update qua WebSocket

Khi be_ai index xong, be_core emit WebSocket event. Subscribe trong detail page:

```typescript
// Thêm vào use-document-detail.ts
import { useSocketEvent } from '@/lib/socket/use-socket-event';
import { WsNamespace } from '@/lib/socket/socket.constants';

// Trong hook:
useSocketEvent(
  WsNamespace.NOTIFICATIONS,
  'document.status_changed',    // Event be_core emit sau khi be_ai callback
  (payload: { documentId: string; status: string }) => {
    if (payload.documentId === documentId) {
      queryClient.invalidateQueries({
        queryKey: ['documents', tenantId, 'detail', documentId],
      });
    }
  },
);
```

> **Lưu ý:** be_core cần emit `document.status_changed` từ `InternalController`
> khi nhận `PATCH /internal/documents/:id/status`. Thêm vào `internal.controller.ts`:
>
> ```typescript
> // Sau khi update status thành công, emit WS event
> this.eventEmitter.emit('document.status_changed', {
>   documentId: id,
>   organizationId: doc.organizationId,
>   status: dto.status,
> });
> ```

---

## Phần B — Document-Scoped Chat

### B1 — Nút "Chat về tài liệu này" trong document detail

**File:** `src/features/tenant/documents/components/documents-detail/document-detail-header.tsx`

```typescript
import { useRouter } from 'next/navigation';
import { useTenantRoute } from '@/features/tenant/hooks/use-tenant-route';

// Trong component:
const router = useRouter();
const { tenantPath } = useTenantRoute();

const handleChatAboutDoc = () => {
  // Tạo conversation mới với context_scope=document, context_id=documentId
  // Sau đó navigate sang chatbot
  router.push(
    `${tenantPath}/chatbot/new?context_scope=document&context_id=${document.id}&context_title=${encodeURIComponent(document.title)}`
  );
};

// Chỉ hiện nút nếu document đã indexed
{document.status === 'indexed' && (
  <Button
    onClick={handleChatAboutDoc}
    variant="outline"
    size="sm"
    className="gap-2"
  >
    <MessageSquare className="h-4 w-4" />
    Chat về tài liệu này
  </Button>
)}
```

### B2 — Xử lý `?context_scope` trong chatbot page

**File:** `src/app/tenant/[tenant]/(dashboard)/chatbot/[conversationId]/page.tsx` và `new/page.tsx`

Khi có `context_scope=document` trong query string:
1. Tạo conversation mới với metadata chứa `context_scope` và `context_id`
2. Hiện badge "Đang chat về: {document title}" trong header

```typescript
// src/features/common/chatbot/hooks/use-chatbot-page.ts

// Trong handleNewChat — đọc context từ URL params
const handleNewChat = async (searchParams?: URLSearchParams) => {
  const contextScope = searchParams?.get('context_scope') ?? 'organization';
  const contextId = searchParams?.get('context_id') ?? undefined;
  const contextTitle = searchParams?.get('context_title') ?? undefined;

  const conv = await createConversation({
    mode: currentMode,
    metadata: {
      context_scope: contextScope,
      context_id: contextId,
      context_title: contextTitle,
    },
  });

  router.push(`${tenantPath}/chatbot/${conv.id}`);
};
```

### B3 — Scope selector trong chatbot sidebar (Option B)

**File:** `src/features/common/chatbot/components/chatbot-scope-selector.tsx` ← MỚI

```typescript
'use client';

import { useState } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

export interface ChatScope {
  type: 'organization' | 'project' | 'document';
  id?: string;
  label: string;
}

interface ChatbotScopeSelectorProps {
  currentScope: ChatScope;
  onScopeChange: (scope: ChatScope) => void;
}

export function ChatbotScopeSelector({
  currentScope,
  onScopeChange,
}: ChatbotScopeSelectorProps) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b">
      <span className="text-xs text-muted-foreground">Phạm vi:</span>
      <Select
        value={currentScope.type}
        onValueChange={(val) =>
          onScopeChange({ type: val as ChatScope['type'], label: val })
        }
      >
        <SelectTrigger className="h-7 text-xs w-auto">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="organization">Toàn tổ chức</SelectItem>
          <SelectItem value="project">Theo dự án</SelectItem>
          {currentScope.type === 'document' && (
            <SelectItem value="document">
              Tài liệu: {currentScope.label}
            </SelectItem>
          )}
        </SelectContent>
      </Select>
    </div>
  );
}
```

### B4 — Hiển thị context badge trong chatbot header

```typescript
// Trong chatbot header component — hiện badge nếu có context
{conv.metadata?.context_scope === 'document' && (
  <Badge variant="outline" className="gap-1 text-xs">
    <FileText className="h-3 w-3" />
    {conv.metadata.context_title ?? 'Tài liệu'}
  </Badge>
)}
```

---

## Phần C — Citation Click-through

### C1 — Link citation về document detail

**File:** `src/features/common/chatbot/components/chatbot-message-list.tsx`

Tìm phần render citation và thêm link:

```typescript
import { useTenantRoute } from '@/features/tenant/hooks/use-tenant-route';
import Link from 'next/link';

// Trong citation render:
{citations.map((citation) => (
  <Link
    key={citation.chunk_index}
    href={`${tenantPath}/documents/${citation.document_id}`}
    target="_blank"
    className="flex items-center gap-2 p-2 rounded border hover:bg-muted/50 transition-colors"
  >
    <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
    <div className="min-w-0">
      <p className="text-xs font-medium truncate">{citation.file_name}</p>
      <p className="text-xs text-muted-foreground truncate">
        {citation.cited_content?.slice(0, 80)}...
      </p>
    </div>
    <span className="text-xs text-muted-foreground shrink-0">
      {Math.round((citation.relevance_score ?? 0) * 100)}%
    </span>
  </Link>
))}
```

---

## Thứ tự implement trong file này

```
1. A1 + A2: Tạo AiService.triggerIngest() và useTriggerChunking()
2. A3: Sửa document-detail-page.tsx — wire button
3. A4: WebSocket status update
4. B1: Nút "Chat về tài liệu này" trong detail header
5. B2: Xử lý context_scope trong chatbot
6. B3: Scope selector (optional, làm sau)
7. C1: Citation link
```

---

## Checklist file này

- [ ] `AiService.triggerIngest()` gọi đúng endpoint be_ai (`POST /api/v1/ingest/documents`)
- [ ] Nút "Trigger chunking" chỉ hiện khi `status IN (not_indexed, pending, failed)`
- [ ] Nút disabled khi `status === processing` hoặc đang `isPending`
- [ ] WebSocket event `document.status_changed` invalidate đúng query key
- [ ] Nút "Chat về tài liệu này" chỉ hiện khi `status === indexed`
- [ ] `createConversation` truyền `metadata.context_scope` và `metadata.context_id`
- [ ] Context badge hiện đúng trong chatbot header
- [ ] Citation có link dẫn đến `/tenant/.../documents/{document_id}`
