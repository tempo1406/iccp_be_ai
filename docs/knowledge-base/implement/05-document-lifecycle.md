# 05 — Document Lifecycle: Delete / Version / Metadata Sync

> **Repo:** `iccp_be_core` (chính) + `iccp_be_ai` (nhận callbacks)
> **Prerequisite:** File 01 (internal routes đã có)
> **Files sửa:** `core.service.ts` trong be_core

---

## Mục tiêu

Đảm bảo khi document thay đổi trạng thái trong be_core, vectors trong Pinecone và OpenSearch
được đồng bộ ngay lập tức. Không để stale chunks tồn tại.

---

## Tổng hợp các trường hợp

| Sự kiện | be_core service method | Hành động cần thêm |
|---|---|---|
| Hard delete | `deletePermanent()` | Gọi be_ai xóa vectors Pinecone + OpenSearch |
| Soft delete | `deactivate()` | Không cần — `batch-access-check` đã block qua `isActive` |
| Restore | `restore()` | Không cần — chunks vẫn còn hoặc user trigger chunking lại |
| Upload version mới | `uploadNewVersion()` | KHÔNG gọi gì — be_ai tự xóa chunks cũ khi re-index |
| Update accessScope / folderId | `update()` | Gọi be_ai sync metadata |

> **Lý do soft delete không cần xóa vectors:**
> `batch-access-check` check `isActive=true`. Doc bị soft-delete sẽ luôn trong `denied`.
> Xóa vectors ngay sẽ phức tạp hơn và không cần thiết vì ACL đã block.

---

## Bước 1 — Thêm `AiSyncService` vào be_core

Service này wrap tất cả HTTP calls từ be_core sang be_ai.

**File:** `src/modules/document/internal/ai-sync.service.ts`

```typescript
import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { HttpService } from '@nestjs/axios';
import { firstValueFrom } from 'rxjs';

@Injectable()
export class AiSyncService {
  private readonly logger = new Logger(AiSyncService.name);
  private readonly aiBaseUrl: string;
  private readonly internalKey: string;

  constructor(
    private readonly httpService: HttpService,
    private readonly config: ConfigService,
  ) {
    this.aiBaseUrl = this.config.get<string>('app.aiBaseUrl', 'http://iccp-be-ai:8001');
    this.internalKey = this.config.get<string>('app.internalApiKey', '');
  }

  private get headers() {
    return { 'x-internal-key': this.internalKey };
  }

  /**
   * Xóa toàn bộ vectors của document trong Pinecone + OpenSearch.
   * Gọi khi hard delete.
   * Không throw nếu be_ai fail — log warning, không block hard delete.
   */
  async deleteDocumentVectors(documentId: string, organizationId: string): Promise<void> {
    try {
      await firstValueFrom(
        this.httpService.delete(
          `${this.aiBaseUrl}/api/v1/ingest/documents/${documentId}`,
          {
            headers: this.headers,
            data: { organization_id: organizationId },
            timeout: 5000,
          },
        ),
      );
      this.logger.log(`Vectors deleted for document ${documentId}`);
    } catch (err) {
      // Không block hard delete — chỉ log warning
      this.logger.warn(
        `Failed to delete vectors for ${documentId}: ${err?.message}. ` +
        `Vectors may remain stale in Pinecone/OpenSearch.`
      );
    }
  }

  /**
   * Sync metadata (accessScope, folderId) vào Pinecone + OpenSearch.
   * Gọi khi update accessScope hoặc folderId.
   */
  async syncDocumentMetadata(
    documentId: string,
    updates: { accessScope?: string; folderId?: string },
  ): Promise<void> {
    try {
      await firstValueFrom(
        this.httpService.patch(
          `${this.aiBaseUrl}/internal/documents/${documentId}/metadata`,
          updates,
          { headers: this.headers, timeout: 5000 },
        ),
      );
    } catch (err) {
      this.logger.warn(
        `Failed to sync metadata for ${documentId}: ${err?.message}`
      );
    }
  }
}
```

---

## Bước 2 — Sửa `deletePermanent` trong `core.service.ts`

```typescript
// Inject AiSyncService
constructor(
  // ... existing injections ...
  private readonly aiSync: AiSyncService,
) {}

async deletePermanent(documentId: string, userId: string): Promise<void> {
  const doc = await this.assertDocumentAccess(
    documentId,
    userId,
    ResourcePermission.MANAGE,
  );
  if (doc.isActive) throw new ValidationException(ErrorCode.DOC001);

  const versions = await this.versionRepo.find({ where: { documentId } });

  // Xóa file trên ImageKit song song
  await Promise.all(
    versions.map((v) =>
      this.imagekitService
        .deleteFileByUrl({ url: v.filePath })
        .catch((err) => {
          console.error(`Failed to delete ImageKit file: ${v.filePath}`, err);
        }),
    ),
  );

  // Xóa record trong DB
  await this.dataSource.transaction(async (manager) => {
    await manager.delete(DocumentVersion, { documentId });
    await manager.delete(Document, { id: documentId });
  });

  // Xóa vectors trong Pinecone + OpenSearch (sau khi DB xóa thành công)
  // Không await — fire-and-forget, không block response
  this.aiSync
    .deleteDocumentVectors(documentId, doc.organizationId ?? '')
    .catch(() => {}); // đã log trong service
}
```

---

## Bước 3 — Sửa `update` trong `core.service.ts`

Khi `accessScope` hoặc `folderId` thay đổi, cần sync metadata vào Pinecone/OpenSearch
để pre-filter được đúng:

```typescript
async update(
  documentId: string,
  userId: string,
  dto: UpdateDocumentRequest,
): Promise<DocumentResponse> {
  const doc = await this.assertDocumentAccess(
    documentId,
    userId,
    ResourcePermission.EDIT,
  );
  if (!doc.isActive) throw new ValidationException(ErrorCode.DOC001);

  // Track những field nào thay đổi để sync
  const metadataChanged =
    (dto.accessScope !== undefined && dto.accessScope !== doc.accessScope) ||
    (dto.folderId !== undefined && dto.folderId !== doc.folderId);

  if (dto.title !== undefined) doc.title = dto.title;
  if (dto.description !== undefined) doc.description = dto.description;
  if (dto.folderId !== undefined) doc.folderId = dto.folderId;
  if (dto.categoryId !== undefined) doc.categoryId = dto.categoryId;
  if (dto.accessScope !== undefined) doc.accessScope = dto.accessScope;

  const saved = await this.documentRepo.save(doc);

  // Sync metadata sang be_ai nếu access control field thay đổi
  if (metadataChanged && doc.status === DocumentStatus.INDEXED) {
    this.aiSync
      .syncDocumentMetadata(documentId, {
        accessScope: dto.accessScope,
        folderId: dto.folderId,
      })
      .catch(() => {});
  }

  return plainToInstance(DocumentResponse, saved, {
    excludeExtraneousValues: true,
  });
}
```

---

## Bước 4 — `uploadNewVersion` — không cần sửa be_core

Khi upload version mới, be_core set `status=PENDING` và `filePath` mới.
be_ai tự xóa chunks cũ và re-index khi được trigger (xem file 02).
**Không cần thêm gì vào `uploadNewVersion`.**

Chỉ cần đảm bảo:
- `IngestionAgent._delete_old_chunks()` chạy đúng (đã làm trong file 02)
- User trigger chunking sau khi upload version mới (file 06)

---

## Bước 5 — Đăng ký `AiSyncService` vào module

**File:** `src/modules/document/internal/internal.module.ts`

```typescript
import { HttpModule } from '@nestjs/axios';
import { AiSyncService } from './ai-sync.service';

@Module({
  imports: [
    TypeOrmModule.forFeature([Document]),
    HttpModule,   // ← thêm
  ],
  controllers: [InternalController],
  providers: [InternalService, AiSyncService],
  exports: [AiSyncService],   // ← export để DocumentCoreModule dùng
})
export class InternalModule {}
```

**File:** `src/modules/document/core/core.module.ts` — import `InternalModule`:

```typescript
@Module({
  imports: [
    TypeOrmModule.forFeature([Document, DocumentVersion, DocumentChunk, DocumentAccessRule]),
    FolderModule,
    InternalModule,   // ← thêm để inject AiSyncService
  ],
  controllers: [DocumentCoreController],
  providers: [DocumentCoreService],
})
export class DocumentCoreModule {}
```

---

## Bước 6 — Thêm `aiBaseUrl` vào config

**File:** `src/configs/app.config.ts`

```typescript
export default registerAs('app', () => ({
  // ... existing ...
  internalApiKey: process.env.INTERNAL_API_KEY ?? '',
  aiBaseUrl: process.env.AI_BASE_URL ?? 'http://iccp-be-ai:8001',
}));
```

**File:** `.env` trong be_core — thêm:
```
AI_BASE_URL=http://iccp-be-ai:8001
```

---

## Tóm tắt lifecycle đầy đủ

```
Upload mới
    └─ status = not_indexed
    └─ (User bấm trigger) → be_ai ingest → status = indexed

Upload version mới
    └─ status = pending, filePath mới
    └─ (User bấm trigger) → be_ai XÓA chunks cũ → index lại → status = indexed

Soft delete (recovery)
    └─ isActive = false
    └─ batch-access-check block tự động (không cần xóa vectors)

Restore từ recovery
    └─ isActive = true
    └─ Nếu status = indexed → chunks vẫn còn → chat được ngay
    └─ Nếu status ≠ indexed → user trigger chunking lại

Hard delete (permanent)
    └─ Xóa DB + ImageKit
    └─ be_core gọi be_ai xóa vectors (fire-and-forget)

Update accessScope / folderId
    └─ be_core cập nhật DB
    └─ be_core sync be_ai metadata (fire-and-forget)
    └─ batch-access-check vẫn là source of truth
```

---

## Checklist file này

- [ ] `AiSyncService` tạo và inject đúng vào `DocumentCoreService`
- [ ] `deletePermanent` gọi `aiSync.deleteDocumentVectors` sau khi DB delete thành công
- [ ] `update` gọi `aiSync.syncDocumentMetadata` chỉ khi `accessScope` hoặc `folderId` thay đổi
- [ ] Cả 2 đều fire-and-forget (không block response chính)
- [ ] `AI_BASE_URL` có trong `.env` của be_core
- [ ] `InternalModule` export `AiSyncService`, `DocumentCoreModule` import `InternalModule`
