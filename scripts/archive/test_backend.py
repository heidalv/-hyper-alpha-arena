import sys
sys.path.insert(0, '.')

print("Testing backend imports...")

try:
    print("1. Importing FastAPI...")
    from fastapi import FastAPI
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")

try:
    print("2. Importing sqlalchemy...")
    import sqlalchemy
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")

try:
    print("3. Importing backend.main...")
    from backend import main
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    import traceback
    traceback.print_exc()

try:
    print("4. Creating engine...")
    from backend.database.connection import engine
    print(f"   Database URL: {engine.url}")
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\nAll tests completed!")
