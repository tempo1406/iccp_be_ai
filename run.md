# iccp_be_ai — Hướng dẫn chạy dự án

## Lệnh chạy chính

```bash
docker compose --env-file .env.dev up --build
```

Chạy nền (không block terminal):
```bash
docker compose --env-file .env.dev up --build -d
```

---

## Manual deploy Production (server)

```bash
# 1. Vào thư mục dự án
cd /path/to/iccp_be_ai

# 2. Down stack production
docker compose -f docker-compose.prod.yaml --env-file .env.prod down --remove-orphans

# 3. Up lại production (build lại image)
docker compose -f docker-compose.prod.yaml --env-file .env.prod up -d --build

# 4. Theo dõi log sau khi up
docker compose -f docker-compose.prod.yaml --env-file .env.prod logs -f --tail=200
```

Xóa luôn volumes (Mongo/Redis) khi cần reset dữ liệu:
```bash
docker compose -f docker-compose.prod.yaml --env-file .env.prod down -v --remove-orphans
```

> Nếu server dùng binary cũ, thay `docker compose` bằng `docker-compose`.

> Nếu cần full CORS trên prod, đặt `CORS_ALLOW_ORIGINS=*` trong `.env.prod` rồi up lại stack.

### Kết nối MongoDB Compass từ máy local (qua SSH tunnel)

MongoDB prod được bind ở VPS localhost (`127.0.0.1:${MONGODB_EXPOSE_PORT}`), không public ra Internet.

Trên máy local, mở tunnel:
```bash
ssh -N -L 27018:127.0.0.1:${MONGODB_EXPOSE_PORT} <VPS_USER>@<VPS_HOST>
```

Sau đó mở MongoDB Compass với URI:
```text
mongodb://127.0.0.1:27018/iccp_ai
```

Nếu dùng user/password:
```text
mongodb://<username>:<password>@127.0.0.1:27018/iccp_ai?authSource=admin
```

---

## Setup lần đầu

```bash
# 1. Tạo shared Docker network (chỉ làm 1 lần)
docker network create iccp-network

# 2. Copy file env
cp .env.example .env.dev
# Sau đó điền các giá trị vào .env.dev (xem bảng bên dưới)
```

> **Lưu ý:** `iccp_be_core` phải đang chạy trước vì AI service giao tiếp qua `iccp-network`.

---

## Biến môi trường (`.env.dev`)

| Biến | Giá trị | Mô tả |
|---|---|---|
| `APP_PORT` | `8001` | Host port ánh xạ vào container |
| `ENVIRONMENT` | `dev` | Môi trường chạy |
| `LOG_LEVEL` | `INFO` | Mức log |
| `GEMINI_API_KEY` | `AIzaSyAjqxNLXAb9SRu0kI4Qmcd4g3ZSZtBMwQQ` | Google Gemini API key |
| `GEMINI_EMBEDDING_MODEL` | `models/text-embedding-004` | Model embedding |
| `GEMINI_CHAT_MODEL` | `gemini-2.5-flash` | Model chat |
| `GEMINI_MAX_TOKENS` | `4096` | Số token tối đa mỗi response |
| `PINECONE_API_KEY` | `pcsk_4EFacw_...` | Pinecone vector DB API key |
| `PINECONE_INDEX_NAME` | `iccp-knowledge` | Tên index Pinecone |
| `PINECONE_ENVIRONMENT` | `us-east-1-aws` | Region Pinecone |
| `MONGODB_URL` | `mongodb://mongo:27017` | Kết nối MongoDB (container nội bộ) |
| `MONGODB_DATABASE` | `iccp_ai` | Tên database MongoDB |
| `MONGODB_EXPOSE_PORT` | `27018` | Host port expose MongoDB trên VPS (bind localhost) |
| `REDIS_URL` | `redis://:12345@103.90.225.95:6380/0` | Redis server ngoài (external) |
| `CELERY_BROKER_URL` | `redis://:12345@103.90.225.95:6380/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://:12345@103.90.225.95:6380/2` | Celery result backend |
| `BE_CORE_BASE_URL` | `http://iccp-be-core:3333` | URL nội bộ tới iccp_be_core |
| `INTERNAL_API_KEY` | `super-secret-internal-key` | API key giao tiếp nội bộ |
| `BE_CORE_INTROSPECT_URL` | `http://iccp-be-core:3333/api/v1/auth/introspect` | Endpoint introspect token |
| `CHUNK_SIZE` | `512` | Kích thước chunk khi index tài liệu |
| `CHUNK_OVERLAP` | `64` | Độ overlap giữa các chunk |
| `RETRIEVAL_TOP_K` | `6` | Số chunk trả về khi RAG |
| `ENABLE_RERANKING` | `false` | Bật/tắt reranking |
| `ENABLE_QUERY_EXPANSION` | `false` | Bật/tắt query expansion |
| `ENABLE_ANALYTICS_AGENT` | `true` | Bật/tắt analytics agent |

---

## Services & Ports

| Container | Host Port | Mô tả |
|---|---|---|
| `iccp-be-ai` | `8001` | FastAPI server |
| `iccp_ai_mongo` | `27018` | MongoDB |
| `iccp-ai-celery-worker` | — | Celery worker (background) |
| `iccp_ai_opensearch` | `9200` | OpenSearch (lexical search / BM25) |
| Redis | `103.90.225.95:6380` | External Redis server |

- Swagger UI: http://localhost:8001/docs
- ReDoc: http://localhost:8001/redoc
- Health check: http://localhost:8001/health
- OpenSearch health: http://localhost:9200/_cluster/health

> **Lưu ý OpenSearch trên Linux:** Nếu gặp lỗi `max virtual memory areas vm.max_map_count [65530] is too low`, chạy:
> ```bash
> sudo sysctl -w vm.max_map_count=262144
> # Để persist sau reboot, thêm vào /etc/sysctl.conf:
> echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
> ```
> Trên **Mac Docker Desktop** thì không cần làm bước này.

---

## Xem log

```bash
# Xem log tất cả services (real-time)
docker compose --env-file .env.dev logs -f

# Chỉ xem log FastAPI server
docker compose --env-file .env.dev logs -f iccp-be-ai

# Chỉ xem log Celery worker
docker compose --env-file .env.dev logs -f celery-worker

# Chỉ xem log MongoDB
docker compose --env-file .env.dev logs -f mongo

# Xem 100 dòng log gần nhất
docker compose --env-file .env.dev logs --tail=100 iccp-be-ai
```

---

## Lệnh hữu ích

```bash
# Dừng tất cả services
docker compose --env-file .env.dev down

# Rebuild lại image (sau khi thay đổi code/dependencies)
docker compose --env-file .env.dev up --build

# Chỉ rebuild 1 service
docker compose --env-file .env.dev up --build iccp-be-ai

# Xem trạng thái các container
docker compose --env-file .env.dev ps

# Vào shell của container API
docker exec -it iccp-be-ai bash

# Kiểm tra health
curl http://localhost:8001/health
```
