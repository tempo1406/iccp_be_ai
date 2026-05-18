# 01 — be_core: Internal Routes cho be_ai

> **Repo:** `iccp_be_core`
> **Prerequisite:** Không có
> **Được dùng bởi:** file 02, 04, 05

---

## Mục tiêu

Tạo module `internal` trong be_core để be_ai gọi vào qua `X-Internal-Key` header.
Không expose ra ngoài internet — chỉ dùng trong Docker network `iccp-network`.

---

## Các endpoints cần tạo

| Method | Path | Dùng khi nào |
|---|---|---|
| `GET` | `/internal/documents/:id` | be_ai lấy thông tin doc (filePath, status, accessScope) |
| `PATCH` | `/internal/documents/:id/status` | be_ai cập nhật status sau khi index xong |
| `POST` | `/internal/documents/batch-access-check` | be_ai verify quyền nhiều doc cùng lúc (ACL live check) |
| `PATCH` | `/internal/documents/:id/metadata` | be_ai sync khi accessScope/folderId thay đổi |
| `DELETE` | `/internal/documents/:id/vectors` | be_core báo be_ai xóa vectors khi hard delete |

---

## Bước 1 — Tạo `InternalKeyGuard`

Guard này verify `X-Internal-Key` header. Không dùng JWT.

**File:** `src/guards/internal-key.guard.ts`

```typescript
import { CanActivate, ExecutionContext, Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Request } from 'express';

@Injectable()
export class InternalKeyGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<Request>();
    const key = req.headers['x-internal-key'];
    const expected = this.config.get<string>('app.internalApiKey');
    if (!key || key !== expected) {
      throw new UnauthorizedException('Invalid internal key');
    }
    return true;
  }
}
```

> **Rule:** `InternalKeyGuard` chỉ dùng cho `/internal/*` prefix. KHÔNG đặt làm global guard.

---

## Bước 2 — DTO

**File:** `src/modules/document/internal/dto/batch-access-check.request.ts`

```typescript
import { IsArray, IsString, IsUUID } from 'class-validator';

export class BatchAccessCheckRequest {
  @IsUUID()
  userId: string;

  @IsUUID()
  organizationId: string;

  @IsArray()
  @IsString({ each: true })
  documentIds: string[];

  // Role IDs của user (lấy từ JWT claims của be_ai)
  @IsArray()
  @IsString({ each: true })
  roleIds: string[];

  // Project IDs của user (lấy từ JWT claims của be_ai)
  @IsArray()
  @IsString({ each: true })
  projectIds: string[];
}
```

**File:** `src/modules/document/internal/dto/update-status.request.ts`

```typescript
import { IsEnum, IsOptional, IsString } from 'class-validator';
import { DocumentStatus } from '../core/entities/document.entity';

export class UpdateDocumentStatusRequest {
  @IsEnum(DocumentStatus)
  status: DocumentStatus;

  @IsOptional()
  @IsString()
  errorMessage?: string;

  @IsOptional()
  indexedChunks?: number;
}
```

**File:** `src/modules/document/internal/dto/sync-metadata.request.ts`

```typescript
import { IsEnum, IsOptional, IsUUID } from 'class-validator';

export class SyncDocumentMetadataRequest {
  @IsOptional()
  @IsEnum(['organization', 'project', 'role', 'user'])
  accessScope?: string;

  @IsOptional()
  @IsUUID()
  folderId?: string;
}
```

---

## Bước 3 — `InternalService`

**File:** `src/modules/document/internal/internal.service.ts`

```typescript
import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Document, DocumentStatus } from '../core/entities/document.entity';
import { BatchAccessCheckRequest } from './dto/batch-access-check.request';

@Injectable()
export class InternalService {
  constructor(
    @InjectRepository(Document)
    private readonly documentRepo: Repository<Document>,
  ) {}

  // ── GET /internal/documents/:id ──────────────────────────────────────
  async getDocumentInfo(id: string) {
    const doc = await this.documentRepo.findOne({ where: { id } });
    if (!doc) throw new NotFoundException(`Document ${id} not found`);
    return {
      id: doc.id,
      organizationId: doc.organizationId,
      title: doc.title,
      filePath: doc.filePath,
      fileName: doc.fileName,
      mimeType: doc.mimeType,
      fileType: doc.fileType,
      accessScope: doc.accessScope,
      folderId: doc.folderId,
      isActive: doc.isActive,
      status: doc.status,
      version: doc.version,
    };
  }

  // ── PATCH /internal/documents/:id/status ─────────────────────────────
  async updateStatus(
    id: string,
    status: DocumentStatus,
    errorMessage?: string,
    indexedChunks?: number,
  ): Promise<void> {
    const doc = await this.documentRepo.findOne({ where: { id } });
    if (!doc) throw new NotFoundException(`Document ${id} not found`);

    doc.status = status;
    if (status === DocumentStatus.INDEXED) {
      doc.indexedAt = new Date();
    }
    if (errorMessage) {
      doc.metadata = { ...(doc.metadata ?? {}), lastError: errorMessage };
    }
    await this.documentRepo.save(doc);
  }

  // ── POST /internal/documents/batch-access-check ───────────────────────
  // Trả về danh sách allowed / denied dựa trên ACL live DB.
  // Đây là single source of truth — check cả isActive, quyền, tồn tại.
  async batchAccessCheck(dto: BatchAccessCheckRequest): Promise<{
    allowed: string[];
    denied: string[];
  }> {
    if (!dto.documentIds.length) return { allowed: [], denied: [] };

    // Dùng lại logic viewableWhere nhưng filter theo danh sách document_ids
    const qb = this.documentRepo
      .createQueryBuilder('doc')
      .select('doc.id')
      .where('doc.id IN (:...ids)', { ids: dto.documentIds })
      .andWhere('doc.is_active = true')       // soft-deleted → denied
      .andWhere('doc.organization_id = :orgId', { orgId: dto.organizationId })
      .andWhere(
        `(
          doc.access_scope IN ('organization', 'system')
          OR doc.uploaded_by = :userId
          OR (
            doc.folder_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM knowledge.document_folders df
              INNER JOIN project.project_members pm
                ON pm.project_id = df.project_id AND pm.user_id = :userId
              WHERE df.id = doc.folder_id AND df.project_id IS NOT NULL
            )
          )
          OR EXISTS (
            SELECT 1 FROM knowledge.document_access_rules dar
            WHERE dar.document_id = doc.id
              AND (
                (dar.access_type = 'user'    AND dar.access_id = :userId) OR
                (dar.access_type = 'role'    AND dar.access_id IN (:...roleIds)) OR
                (dar.access_type = 'project' AND dar.access_id IN (:...projectIds))
              )
          )
          OR (
            doc.folder_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM knowledge.folder_access_rules far
              INNER JOIN knowledge.document_folders df ON df.id = doc.folder_id
              WHERE (
                far.folder_id = df.id
                OR (df.path IS NOT NULL AND df.path LIKE CONCAT('%', far.folder_id, '%'))
              )
              AND (
                (far.access_type = 'user'    AND far.access_id = :userId) OR
                (far.access_type = 'role'    AND far.access_id IN (:...roleIds)) OR
                (far.access_type = 'project' AND far.access_id IN (:...projectIds))
              )
            )
          )
        )`,
        {
          userId: dto.userId,
          roleIds: dto.roleIds.length ? dto.roleIds : ['__none__'],
          projectIds: dto.projectIds.length ? dto.projectIds : ['__none__'],
        },
      );

    const allowedDocs = await qb.getMany();
    const allowedIds = new Set(allowedDocs.map((d) => d.id));
    const denied = dto.documentIds.filter((id) => !allowedIds.has(id));

    return {
      allowed: [...allowedIds],
      denied,
    };
  }

  // ── PATCH /internal/documents/:id/metadata ───────────────────────────
  async syncMetadata(
    id: string,
    accessScope?: string,
    folderId?: string,
  ): Promise<{ synced: boolean }> {
    const doc = await this.documentRepo.findOne({ where: { id } });
    if (!doc) return { synced: false };

    if (accessScope) doc.accessScope = accessScope as any;
    if (folderId !== undefined) doc.folderId = folderId;
    await this.documentRepo.save(doc);

    return { synced: true };
  }
}
```

> **Rule:** `batchAccessCheck` dùng query SQL giống hệt `viewableWhere()` trong `core.service.ts`
> để đảm bảo 2 nơi check cùng logic. Nếu sửa logic ACL ở một chỗ, phải sửa cả hai.

---

## Bước 4 — `InternalController`

**File:** `src/modules/document/internal/internal.controller.ts`

```typescript
import {
  Body, Controller, Delete, Get, Param, Patch, Post, UseGuards,
} from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { InternalKeyGuard } from '@guards/internal-key.guard';
import { InternalService } from './internal.service';
import { BatchAccessCheckRequest } from './dto/batch-access-check.request';
import { UpdateDocumentStatusRequest } from './dto/update-status.request';
import { SyncDocumentMetadataRequest } from './dto/sync-metadata.request';
import { DocumentStatus } from '../core/entities/document.entity';

@ApiTags('Internal')
@UseGuards(InternalKeyGuard)
@Controller('internal/documents')
export class InternalController {
  constructor(private readonly internalService: InternalService) {}

  @Get(':id')
  getDocumentInfo(@Param('id') id: string) {
    return this.internalService.getDocumentInfo(id);
  }

  @Patch(':id/status')
  updateStatus(
    @Param('id') id: string,
    @Body() dto: UpdateDocumentStatusRequest,
  ) {
    return this.internalService.updateStatus(
      id,
      dto.status as DocumentStatus,
      dto.errorMessage,
      dto.indexedChunks,
    );
  }

  @Post('batch-access-check')
  batchAccessCheck(@Body() dto: BatchAccessCheckRequest) {
    return this.internalService.batchAccessCheck(dto);
  }

  @Patch(':id/metadata')
  syncMetadata(
    @Param('id') id: string,
    @Body() dto: SyncDocumentMetadataRequest,
  ) {
    return this.internalService.syncMetadata(id, dto.accessScope, dto.folderId);
  }
}
```

---

## Bước 5 — `InternalModule`

**File:** `src/modules/document/internal/internal.module.ts`

```typescript
import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Document } from '../core/entities/document.entity';
import { InternalController } from './internal.controller';
import { InternalService } from './internal.service';

@Module({
  imports: [TypeOrmModule.forFeature([Document])],
  controllers: [InternalController],
  providers: [InternalService],
})
export class InternalModule {}
```

---

## Bước 6 — Đăng ký vào `DocumentModule`

**File:** `src/modules/document/document.module.ts` — thêm `InternalModule`:

```typescript
import { InternalModule } from './internal/internal.module';

@Module({
  imports: [
    FolderModule,
    CategoryModule,
    DocumentCoreModule,
    AccessModule,
    InternalModule,    // ← thêm dòng này
  ],
})
export class DocumentModule {}
```

---

## Bước 7 — Update `BeCoreClient` trong be_ai

**File:** `app/clients/be_core_client.py` — thêm các method mới:

```python
async def get_document_info(self, document_id: str) -> dict:
    """GET /internal/documents/{id}"""
    resp = await self._client.get(
        f"{settings.BE_CORE_BASE_URL}/internal/documents/{document_id}",
        headers=self._internal_headers(),
    )
    resp.raise_for_status()
    return resp.json()

async def batch_access_check(
    self,
    user_id: str,
    organization_id: str,
    document_ids: list[str],
    role_ids: list[str],
    project_ids: list[str],
) -> dict:
    """POST /internal/documents/batch-access-check"""
    resp = await self._client.post(
        f"{settings.BE_CORE_BASE_URL}/internal/documents/batch-access-check",
        headers=self._internal_headers(),
        json={
            "userId": user_id,
            "organizationId": organization_id,
            "documentIds": document_ids,
            "roleIds": role_ids,
            "projectIds": project_ids,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()  # { "allowed": [...], "denied": [...] }

async def sync_document_metadata(
    self,
    document_id: str,
    access_scope: str | None = None,
    folder_id: str | None = None,
) -> None:
    """PATCH /internal/documents/{id}/metadata"""
    payload = {}
    if access_scope:
        payload["accessScope"] = access_scope
    if folder_id is not None:
        payload["folderId"] = folder_id
    resp = await self._client.patch(
        f"{settings.BE_CORE_BASE_URL}/internal/documents/{document_id}/metadata",
        headers=self._internal_headers(),
        json=payload,
        timeout=5.0,
    )
    resp.raise_for_status()
```

---

## Test thủ công

```bash
# Lấy thông tin doc
curl -X GET http://localhost:3333/internal/documents/{doc_id} \
  -H "x-internal-key: super-secret-internal-key"

# Cập nhật status
curl -X PATCH http://localhost:3333/internal/documents/{doc_id}/status \
  -H "x-internal-key: super-secret-internal-key" \
  -H "Content-Type: application/json" \
  -d '{"status": "indexed", "indexedChunks": 12}'

# Batch access check
curl -X POST http://localhost:3333/internal/documents/batch-access-check \
  -H "x-internal-key: super-secret-internal-key" \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "user-uuid",
    "organizationId": "org-uuid",
    "documentIds": ["doc-uuid-1", "doc-uuid-2"],
    "roleIds": ["role-uuid-1"],
    "projectIds": []
  }'
# Expected: { "allowed": ["doc-uuid-1"], "denied": ["doc-uuid-2"] }
```

---

## Checklist file này

- [ ] `InternalKeyGuard` tạo và chỉ dùng cho `/internal/*`
- [ ] `batchAccessCheck` dùng cùng logic ACL với `viewableWhere()` trong `core.service.ts`
- [ ] `batchAccessCheck` check cả `isActive = true`
- [ ] `InternalModule` đăng ký vào `DocumentModule`
- [ ] `BeCoreClient` trong be_ai có đủ 3 method mới
- [ ] Test thủ công với `soft-deleted` doc trả về `denied`
- [ ] Test thủ công với doc user không có quyền trả về `denied`
