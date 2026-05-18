# ICCP AI Service — Hướng dẫn chạy dự án

## 1. Yêu cầu trước khi bắt đầu

| Công cụ | Phiên bản | Dùng để làm gì |
|---------|-----------|----------------|
| Python | 3.11+ | Chạy local không Docker |
| Docker Desktop | 24+ | Chạy toàn bộ service bằng container |
| Docker Compose | v2+ | Orchestrate multi-container |
| Git | bất kỳ | Clone source |

---

## 2. Lấy API Keys ở đâu?

### 2.1 OpenAI API Key
1. Vào [https://platform.openai.com](https://platform.openai.com)
2. Đăng nhập → vào **API Keys** (góc trên phải avatar → View API keys)
3. Nhấn **"Create new secret key"**
4. Copy ngay vì chỉ hiện 1 lần
5. Đặt vào env: `OPENAI_API_KEY=sk-...`

> **Lưu ý model:** Service dùng `gpt-4o-mini` (rẻ hơn gpt-4o ~15x) và `text-embedding-3-small`. Cần có billing đã setup.

### 2.2 Pinecone API Key
1. Vào [https://app.pinecone.io](https://app.pinecone.io)
2. Đăng ký tài khoản (có free tier)
3. Vào **API Keys** ở sidebar trái
4. Copy key sẵn có hoặc nhấn **"Generate Key"**
5. Đặt vào env: `PINECONE_API_KEY=pcsk_...`

> **Tạo Index:** Service tự tạo index `iccp-knowledge` lúc khởi động nếu chưa tồn tại (serverless, dim=1536, cosine).

### 2.3 JWT Secret
- Phải **giống hệt** với `JWT_SECRET` trong `iccp_be_core`
- Mở file `.env.dev` của `iccp_be_core`, copy giá trị `JWT_SECRET`
- Đặt vào `iccp_be_ai`: `JWT_SECRET=<same_value>`

### 2.4 Internal API Key
- Tự đặt một chuỗi bất kỳ (ví dụ generate UUID)
- Phải **giống hệt** ở cả 2 service:
  - `iccp_be_core`: dùng để gọi `iccp_be_ai`
  - `iccp_be_ai`: dùng để xác thực request đến

```bash
# Gợi ý generate:
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Cấu hình file `.env`

### Bước 1: Copy file mẫu
```bash
cd /path/to/iccp_be_ai
cp .env.example .env.dev
```

### Bước 2: Điền các giá trị

```env
# ── App ──────────────────────────────────────────────────
APP_PORT=8001
ENVIRONMENT=dev
LOG_LEVEL=INFO

# ── OpenAI ───────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx    # lấy từ platform.openai.com
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=2048

# ── Pinecone ─────────────────────────────────────────────
PINECONE_API_KEY=pcsk_xxxxxxxxxxxxxxxxxxxxxxxx     # lấy từ app.pinecone.io
PINECONE_INDEX_NAME=iccp-knowledge                 # giữ nguyên
PINECONE_ENVIRONMENT=us-east-1-aws                 # giữ nguyên (serverless)

# ── PostgreSQL (dùng chung với iccp_be_core) ──────────────
# Nếu chạy Docker: dùng tên container postgres của iccp_be_core
POSTGRES_URL=postgresql+asyncpg://iccp_user:iccp_pass@postgres:5432/iccp_db

# Nếu chạy local (không Docker):
# POSTGRES_URL=postgresql+asyncpg://iccp_user:iccp_pass@localhost:5433/iccp_db

# ── Redis ────────────────────────────────────────────────
# Nếu chạy Docker:
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# Nếu chạy local (không Docker, redis của iccp_be_ai chạy port 6380):
# REDIS_URL=redis://localhost:6380/0
# CELERY_BROKER_URL=redis://localhost:6380/1
# CELERY_RESULT_BACKEND=redis://localhost:6380/2

# ── Internal API ─────────────────────────────────────────
BE_CORE_BASE_URL=http://iccp-be-core:3000          # tên container iccp_be_core
INTERNAL_API_KEY=<chuỗi_bí_mật_tự_đặt>            # phải khớp với iccp_be_core

# ── JWT (phải giống hệt iccp_be_core) ───────────────────
JWT_SECRET=<copy_từ_iccp_be_core_.env>
JWT_ALGORITHM=HS256

# ── RAG tuning (giữ mặc định nếu không chắc) ────────────
CHUNK_SIZE=512
CHUNK_OVERLAP=64
RETRIEVAL_TOP_K=6

# ── Feature flags ────────────────────────────────────────
ENABLE_RERANKING=false
ENABLE_QUERY_EXPANSION=false
ENABLE_ANALYTICS_AGENT=true
```

---

## 4. Chạy trên môi trường LOCAL (Docker)

### Bước 1: Tạo shared network (chỉ cần làm 1 lần)
```bash
docker network create iccp-network
```
> Network này để `iccp_be_core` và `iccp_be_ai` liên lạc với nhau qua tên container.

### Bước 2: Đảm bảo `iccp_be_core` đang chạy
```bash
cd ../iccp_be_core
docker compose --env-file .env.dev up -d
```
> PostgreSQL của `iccp_be_core` phải đang chạy vì `iccp_be_ai` dùng chung DB.

### Bước 3: Chạy `iccp_be_ai`
```bash
cd iccp_be_ai

# Build và start
docker compose --env-file .env.dev up --build

# Hoặc chạy nền (detach)
docker compose --env-file .env.dev up --build -d
```

### Bước 4: Chạy migration (lần đầu)
```bash
# Chạy migration để tạo schema ai + bảng ai.ingest_jobs
docker compose exec iccp-be-ai alembic upgrade head
```

### Bước 5: Kiểm tra service đang chạy
```bash
curl http://localhost:8001/health
# → {"status":"ok","service":"iccp_be_ai","version":"1.0.0"}
```

### Bước 6: Xem logs
```bash
# Xem log tất cả services
docker compose logs -f

# Chỉ xem log API
docker compose logs -f iccp-be-ai

# Chỉ xem log Celery worker
docker compose logs -f celery-worker
```

### Kiểm tra API Docs
Mở trình duyệt: [http://localhost:8001/docs](http://localhost:8001/docs)

---

## 5. Chạy LOCAL không dùng Docker (pure Python)

Dùng khi muốn debug trực tiếp, hot reload nhanh hơn.

### Bước 1: Cài Python 3.11+
```bash
python3 --version  # phải >= 3.11

# Nếu chưa có, cài qua pyenv (khuyến nghị):
pyenv install 3.11.9
pyenv local 3.11.9
```

### Bước 2: Tạo virtual environment
```bash
cd iccp_be_ai
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate.bat     # Windows
```

### Bước 3: Cài dependencies
```bash
pip install -r requirements-dev.txt
```

### Bước 4: Cấu hình env
```bash
cp .env.example .env.dev

# Sửa POSTGRES_URL trỏ về localhost (PostgreSQL của iccp_be_core):
POSTGRES_URL=postgresql+asyncpg://iccp_user:iccp_pass@localhost:5433/iccp_db

# Sửa REDIS_URL trỏ về localhost (Redis của iccp_be_ai chạy port 6380):
REDIS_URL=redis://localhost:6380/0

# Sửa BE_CORE_BASE_URL trỏ về localhost:
BE_CORE_BASE_URL=http://localhost:3000
```

### Bước 5: Start Redis riêng cho iccp_be_ai
```bash
# Dùng Docker để chạy Redis trên port 6380 (tránh conflict với be_core port 6379)
docker run -d --name iccp_ai_redis -p 6380:6379 redis:7-alpine
```

### Bước 6: Chạy migration
```bash
alembic upgrade head
```

### Bước 7: Khởi động API server
```bash
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
```

### Bước 8: Khởi động Celery worker (terminal riêng)
```bash
source .venv/bin/activate

celery -A app.workers.celery_app.celery_app worker \
  --loglevel=info \
  --queues=ingest,analytics \
  --concurrency=2
```

---

## 6. Chạy trên môi trường PRODUCTION

### Bước 1: Tạo file `.env.prod`
```bash
cp .env.example .env.prod
# Điền đầy đủ các biến, đặc biệt:
#   ENVIRONMENT=prod
#   LOG_LEVEL=WARNING
#   OPENAI_API_KEY=sk-...
#   PINECONE_API_KEY=pcsk_...
```

### Bước 2: Build và chạy production
```bash
docker compose --env-file .env.prod -f docker-compose.prod.yaml up --build -d
```

> **Khác biệt so với dev:**
> - Gunicorn + 4 uvicorn workers (thay vì 1 uvicorn --reload)
> - Celery concurrency=4 (thay vì 2)
> - Thêm `celery-beat` container cho scheduled tasks
> - Không mount volume source code
> - Swagger UI bị tắt (docs_url=None)

### Bước 3: Chạy migration production
```bash
docker compose -f docker-compose.prod.yaml exec iccp-be-ai-prod alembic upgrade head
```

### Bước 4: Health check
```bash
curl https://your-domain.com/health
```

---

## 7. Xử lý lỗi thường gặp

### Lỗi: `VectorStoreException: Failed to initialize Pinecone`
- Kiểm tra `PINECONE_API_KEY` đúng chưa
- Kiểm tra internet connection trong container: `docker compose exec iccp-be-ai curl https://api.pinecone.io`

### Lỗi: `Connection refused` khi gọi be_core
- Kiểm tra `BE_CORE_BASE_URL` đúng chưa
- Kiểm tra cả 2 service cùng network `iccp-network`: `docker network inspect iccp-network`
- Kiểm tra container be_core đang chạy: `docker ps | grep iccp-be-core`

### Lỗi: `Invalid token` (401)
- `JWT_SECRET` trong `iccp_be_ai` phải giống hệt trong `iccp_be_core`
- Kiểm tra: `docker compose exec iccp-be-ai printenv JWT_SECRET`

### Lỗi migration: `schema ai does not exist`
```bash
# Chạy lại migration với verbose
docker compose exec iccp-be-ai alembic upgrade head --sql  # xem SQL trước
docker compose exec iccp-be-ai alembic upgrade head
```

### Lỗi: Celery task không chạy
```bash
# Kiểm tra Celery worker có connect được Redis không
docker compose logs celery-worker | grep "Connected to"

# Kiểm tra Redis còn sống
docker compose exec redis redis-cli ping  # → PONG
```

### Lỗi: `EmbeddingException: OpenAI embedding failed`
- Kiểm tra `OPENAI_API_KEY` còn hạn mức không
- Kiểm tra model `text-embedding-3-small` có available không

---

## 8. Cấu trúc Docker network

```
┌─────────────────────────────────────────────────────┐
│              Network: iccp-network                  │
│                                                     │
│  ┌─────────────────┐    ┌──────────────────────┐   │
│  │  iccp-be-core   │    │    iccp-be-ai         │   │
│  │  (port 3000)    │◄──►│    (port 8001)        │   │
│  └────────┬────────┘    └──────────┬───────────┘   │
│           │                        │               │
│  ┌────────▼────────┐    ┌──────────▼───────────┐   │
│  │    postgres      │    │  celery-worker       │   │
│  │    (port 5432)   │    │  (no port exposed)   │   │
│  └─────────────────┘    └──────────┬───────────┘   │
│                                    │               │
│  ┌─────────────────────────────────▼───────────┐   │
│  │         redis (port 6379 internal)          │   │
│  │         exposed: 6380:6379 (host)           │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

> **Note:** `iccp_be_ai` không tự chạy PostgreSQL. Nó dùng container `postgres` từ `iccp_be_core`. Đảm bảo `iccp_be_core` khởi động trước.

---

## 9. Chạy tests

```bash
# Activate venv
source .venv/bin/activate

# Chạy tất cả tests
pytest tests/ -v

# Chạy chỉ unit tests
pytest tests/unit/ -v

# Chạy chỉ integration tests
pytest tests/integration/ -v

# Xem coverage report
pytest tests/ --cov=app --cov-report=html
open htmlcov/index.html
```

---

## 10. Tóm tắt nhanh (Quick Reference)

```bash
# Lần đầu setup
cp .env.example .env.dev           # copy env
docker network create iccp-network  # tạo network

# Chạy dev (có iccp_be_core đang chạy trước)
docker compose --env-file .env.dev up --build

# Migration
docker compose exec iccp-be-ai alembic upgrade head

# Check hoạt động
curl http://localhost:8001/health

# Xem logs
docker compose logs -f

# Dừng
docker compose down

# Dừng + xóa volumes
docker compose down -v
```
