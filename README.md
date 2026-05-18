# ICCP AI Service

RAG Chatbot Multi-Agent service for the ICCP platform, built with FastAPI + LangGraph + Pinecone.

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Pinecone account
- OpenAI API key

## Quick Start (Development)

```bash
# 1. Copy environment file
cp .env.example .env.dev
# Edit .env.dev with your credentials

# 2. Create shared Docker network (if not exists)
docker network create iccp-network

# 3. Run with Docker Compose
docker compose --env-file .env.dev up --build
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| `iccp-be-ai` | 8001 | FastAPI application |
| `celery-worker` | — | Background ingestion worker |
| `redis` | 6380 | Broker + cache |

## API Documentation

- Swagger UI: http://localhost:8001/docs
- ReDoc: http://localhost:8001/redoc

## Key Endpoints

### Ingestion (called by iccp_be_core)
```
POST   /api/v1/ingest/documents           # Trigger document ingestion
POST   /api/v1/ingest/documents/{id}/retry # Retry failed ingestion
DELETE /api/v1/ingest/documents/{id}      # Remove doc vectors from Pinecone
GET    /api/v1/ingest/jobs/{id}           # Check ingestion job status
```

### Chat
```
POST /api/v1/chat/conversations                       # Create conversation
POST /api/v1/chat/conversations/{id}/messages         # Send message (streaming SSE)
GET  /api/v1/chat/conversations/{id}/messages         # Get conversation history
```

## Local Development (without Docker)

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run migrations
alembic upgrade head

# Start API
uvicorn app.main:app --reload --port 8001

# Start Celery worker (separate terminal)
celery -A app.workers.celery_app.celery_app worker --loglevel=info -Q ingest,analytics
```

## Running Tests

```bash
pytest tests/ -v
```

## Environment Variables

See `.env.example` for all required variables.

## Architecture

See `docs/idea.md` for full architecture documentation.
