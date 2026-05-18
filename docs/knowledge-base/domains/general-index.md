# Domain Index: general

## Chu de
- Tai lieu tong quan san pham
- Quy trinh van hanh
- Quy dinh noi bo

## Muc tieu domain
- Lam baseline domain cho pilot ingestion va chat retrieval.
- Dung de xac nhan luong ACL + citation + graph rerank hoat dong dung.

## Entity cot loi
- `organization`
- `user`
- `role`
- `project`
- `document`

## Relation chinh
- `user BELONGS_TO organization`
- `user HAS_ROLE role`
- `role GRANTS permission`
- `document BELONGS_TO organization`
- `document VISIBLE_TO role/project/user`

## Expected relation labels (de thong nhat graph)
- `BELONGS_TO`
- `HAS_ROLE`
- `GRANTS`
- `VISIBLE_TO`

## Tai lieu nguon uu tien ingest

| priority | doc_id | title | source_path | expected_entities | expected_relations | ingest_ready |
|---|---|---|---|---|---|---|
| P0 | KB-GENERAL-0001 | Placeholder document | docs/knowledge-base/sources/placeholder.md | organization,user,role,project,document | BELONGS_TO,HAS_ROLE,GRANTS,VISIBLE_TO | no |

## Chunking huong dan domain
- Uu tien chunk theo heading cap H2/H3.
- Muc tieu 300–600 tokens/chunk, overlap 60–100 tokens.
- Khong cat giua bang thong tin ACL hoac policy.
- Neu chunk den tu OCR, gan `source_type = "ocr_document"` va luu `ocr_confidence`.

## Graph extraction rule toi thieu
- Moi chunk co it nhat 1 lien ket entity neu phat hien duoc term hop le.
- Neu chunk khong co entity, van luu lexical/vector nhung `score_graph = 0`.
- Uu tien extract entity: ten to chuc, ten role, ten project, ten tai lieu.

## ACL mapping rule
- `organization`: ba mang `allowed_*_ids` bat buoc rong `[]`.
- `project`: bat buoc co it nhat 1 phan tu trong `allowed_project_ids`.
- `role`: bat buoc co it nhat 1 phan tu trong `allowed_role_ids`.
- `user`: bat buoc co it nhat 1 phan tu trong `allowed_user_ids`.

## OCR rule cho domain nay
- File PDF co anh scan: dung Tesseract, gan `source_type = "ocr_document"`.
- Chu y: file co ca text va anh (mixed) → chia rieng phan text va OCR phan anh.

## Citation expectation cho domain nay
- Citation bat buoc tra ve `document_id`, `document_title`, `chunk_id`, `chunk_preview`.
- Phai co `source_type`: `text` hoac `ocr_document`.
- Neu chunk den tu OCR, uu tien co `ocr_confidence`.
- `final_score` phai luon co (RRF score, hoac fallback = `score_vector`).

## Domain runbook status
- `draft`: domain vua tao, chua co document san sang.
- `ready`: da co it nhat 1 doc `ingest_ready=yes`.
- `active`: da co doc `indexed` va `graph_linked`.
