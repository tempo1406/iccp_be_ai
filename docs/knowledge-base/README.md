# Knowledge Base Docs Guide

## Muc tieu
- Chuan hoa tai lieu dau vao cho ingestion, retrieval va graph rerank.
- Giup dev/agent implement nhanh voi contract ro rang, han che suy doan.

## Cau truc thu muc
- `hybrid-rag-plan.md`: quyet dinh kien truc, pipeline, contracts va ke hoach trien khai.
- `kb-index.md`: muc luc tong cua tat ca tai lieu curated duoc quan ly trong KB.
- `domains/<domain>-index.md`: muc luc theo domain, dinh nghia entity/relation va rule extraction.

---

## Phan biet 2 he thong status

### 1. be_core `DocumentStatus` (ky thuat — enum trong DB)
Day la trang thai ky thuat cua file document trong he thong core, dung cho tat ca tai lieu user upload:

```
not_indexed → pending → processing → indexed → failed
```

- Chuyen doi boi: be_core service (upload → not_indexed / new_version → pending) va be_ai Celery worker (processing → indexed/failed).
- Ghi trong bang `knowledge.documents`, truong `status`.

### 2. KB tracking status (quan tri — chi trong kb-index.md)
Day la trang thai cua curated content (tai lieu tu viet, wiki noi bo) duoc them thu cong vao KB, KHONG phai DocumentStatus:

**ingest_status:**
- `draft`: moi khai bao, chua xep hang ingest.
- `pending`: da san sang ingest.
- `indexing`: dang parse/chunk/index lexical+vector.
- `indexed`: lexical+vector hoan tat.
- `failed`: loi ky thuat hoac policy.
- `archived`: ngung su dung, giu de audit.

**graph_status:**
- `pending`: chua xu ly graph.
- `linking`: dang tao entity/relation links.
- `linked`: da tao graph links.
- `skipped`: bo qua graph do tai lieu khong phu hop.
- `failed`: loi trong graph extraction/linking.

---

## Quy trinh cap nhat docs (bat buoc)
1. Them hoac cap nhat document trong `kb-index.md`.
2. Dam bao `doc_id` khop voi domain index tuong ung.
3. Cap nhat `ingest_status` va `graph_status` sau moi lan xu ly.
4. Neu doi ACL, cap nhat day du cac cot `allowed_*_ids`.
5. Chi deploy ingestion sau khi docs hop le.

## Validation checklist truoc ingestion
- [ ] `doc_id` duy nhat, dung convention `KB-<DOMAIN>-<NNNN>`.
- [ ] `source_path` ton tai trong repo/storage.
- [ ] `tenant_id` va `visibility_scope` da khai bao.
- [ ] ACL arrays khong mau thuan voi `visibility_scope`.
- [ ] Domain index co `expected_entities` va `expected_relations` cho document uu tien P0/P1.

## Citation rule
- Moi citation tra ve cho web phai map duoc toi `doc_id` co trong `kb-index.md`.
- Citation tu OCR document phai co `source_type = "ocr_document"`.
- Citation tu OCR anh chat phai co `source_type = "ocr_chat_image"`, khong co `document_id`.
