import os
import sys
from sqlalchemy import create_engine

backend_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(backend_dir)

DATABASE_URL = os.path.join(project_dir, 'data', 'alpha_arena.db')
SNAPSHOT_DATABASE_URL = os.path.join(project_dir, 'data', 'alpha_snapshots.db')

print(f"Database URL: sqlite:///{DATABASE_URL}")
print(f"Snapshot URL: sqlite:///{SNAPSHOT_DATABASE_URL}")

main_engine = create_engine(f"sqlite:///{DATABASE_URL}", connect_args={"check_same_thread": False})
snapshot_engine = create_engine(f"sqlite:///{SNAPSHOT_DATABASE_URL}", connect_args={"check_same_thread": False})

from database.models import Base

print("Creating main database tables...")
Base.metadata.create_all(bind=main_engine)
print("Main database tables created!")

print("Creating snapshot database tables...")
from database.snapshot_models import SnapshotBase
SnapshotBase.metadata.create_all(bind=snapshot_engine)
print("Snapshot database tables created!")
