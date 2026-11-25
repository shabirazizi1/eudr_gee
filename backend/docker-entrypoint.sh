#!/bin/sh
set -e

python - <<'PY'
from app.db import init_db, engine
from app.models import Farm
from sqlmodel import Session, select

init_db()
with Session(engine) as session:
    has_data = session.exec(select(Farm)).first() is not None

if not has_data:
    print("No farms found -- seeding demo data")
    import subprocess
    subprocess.run(["python", "-m", "app.seed", "--verify"], check=True)
else:
    print("Database already has data -- skipping seed")
PY

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
