Lệnh cài thư viện vào môi trường venv
source venv/bin/activate && pip install -r requirements-dev.txt


Lệnh chạy ở môi trường local
source venv/bin/activate && uvicorn app.main:app --reload --port 8001

swagger
http://localhost:8001/docs

Migrate database

alembic upgrade head