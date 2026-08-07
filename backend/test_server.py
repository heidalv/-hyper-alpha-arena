from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Test Server")

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "message": "Test server running"}

@app.get("/api/test-account")
async def test_account():
    return {"message": "Account check working", "has_accounts": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")