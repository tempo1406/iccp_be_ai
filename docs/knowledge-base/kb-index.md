# Knowledge Base Index

## Muc dich
- Day la muc luc tong cho toan bo tai lieu curated duoc ingest vao hybrid RAG.
- Dung de quan tri version tai lieu, scope truy cap va doi soat citation.
- Khac voi `DocumentStatus` trong be_core: day la KB tracking status cho curated content.

## Quy tac dat ten
- doc_id: `KB-<DOMAIN>-<NNNN>`
- domain folder: `domains/<domain>-index.md`

## Contract fields bat buoc
- `doc_id`: id on dinh, duy nhat, khong duoc doi sau khi da index.
- `title`: ten tai lieu hien thi cho citation.
- `domain`: nhom nghiep vu.
- `source_path`: duong dan file nguon.
- `tenant_id`: tenant so huu.
- `visibility_scope`: `organization | project | role | user`.
- `allowed_project_ids`: mang project ids (JSON array, co the rong).
- `allowed_role_ids`: mang role ids (JSON array, co the rong).
- `allowed_user_ids`: mang user ids (JSON array, co the rong).
- `owner`: nguoi/chuc nang quan tri tai lieu.
- `ingest_status`: trang thai indexing.
- `graph_status`: trang thai graph linking.
- `last_updated`: ngay cap nhat gan nhat (YYYY-MM-DD).

## Danh sach tai lieu

| doc_id | title | domain | source_path | tenant_id | visibility_scope | allowed_project_ids | allowed_role_ids | allowed_user_ids | owner | ingest_status | graph_status | last_updated |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KB-GENERAL-0001 | Placeholder document | general | docs/knowledge-base/sources/placeholder.md | tenant_demo | organization | [] | [] | [] | ai-team | pending | pending | 2026-04-16 |

## Chu thich ingest_status
- `draft`: moi khai bao, chua cho ingest.
- `pending`: da san sang ingest.
- `indexing`: dang parse/chunk/index lexical+vector.
- `indexed`: lexical+vector hoan tat.
- `failed`: loi ky thuat hoac policy.
- `archived`: ngung su dung, giu de audit.

## Chu thich graph_status
- `pending`: chua xu ly graph.
- `linking`: dang tao entity/relation links.
- `linked`: da tao graph links.
- `skipped`: bo qua graph do tai lieu khong phu hop.
- `failed`: loi trong buoc graph extraction/linking.

## Notes
- Moi document trong citations phai ton tai trong bang tren.
- Khi xoa document, cap nhat `ingest_status=archived`, `graph_status=skipped` va ghi ly do o changelog noi bo.
- `ingest_status` o day la KB tracking status, KHONG phai `DocumentStatus` enum cua be_core.
- Tai lieu user upload qua be_core duoc theo doi qua `Document.status` trong DB, khong can them vao bang nay tru khi la curated content.
