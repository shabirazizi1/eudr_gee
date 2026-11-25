#!/bin/sh
set -e

# Create tables synchronously so there's no race between the backgrounded seed
# check below and uvicorn's own startup -- both call init_db(), which is a no-op
# once the schema exists.
python -c "from app import models; from app.db import init_db; init_db()"

# Seed in the background so uvicorn is reachable immediately; farms appear once
# the Earth Engine seed run finishes (~2 min on first start).
(
python - <<'PY'
from app.db import engine
from app.models import Farm
from sqlmodel import Session, select

with Session(engine) as session:
    has_data = session.exec(select(Farm)).first() is not None

if not has_data:
    print("No farms found -- seeding demo data in the background (Earth Engine, ~2 min)")
    import subprocess
    subprocess.run(["python", "-m", "app.seed", "--verify"], check=True)
else:
    print("Database already has data -- skipping seed")
PY
) &

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
