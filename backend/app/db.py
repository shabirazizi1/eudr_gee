from sqlmodel import Session, SQLModel, create_engine

from . import config

# timeout: bulk screening writes from 3 worker threads, so wait longer than SQLite's 5 s default
engine = create_engine(config.DB_URL, connect_args={"check_same_thread": False, "timeout": 30})


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
