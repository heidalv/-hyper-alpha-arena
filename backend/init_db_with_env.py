import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Now import and run the initialization script
from backend.database import init_postgresql

if __name__ == "__main__":
    init_postgresql.create_tables()
    init_postgresql.verify_setup()
