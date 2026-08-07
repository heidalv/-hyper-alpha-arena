# Port Configuration

## Local Development

```
Desktop Frontend:  http://localhost:5173  (Vite dev server)
Mobile Frontend:   http://localhost:5174  (Vite dev server, PWA)
Backend:           http://localhost:8002  (FastAPI)
Database:          localhost:5432         (PostgreSQL)
```

**Notes:**
- Desktop frontend runs on 5173 (Vite default)
- Mobile frontend runs on 5174 (separate from desktop)
- Backend runs on 8002
- Both frontends proxy /api and /ws to backend
- Start with: `Start-All.bat`

---

## Port Summary

| Service        | Port |
|----------------|------|
| Desktop FE     | 5173 |
| Mobile FE      | 5174 |
| Backend        | 8002 |
| Database       | 5432 |

---

## Why Different Frontend Ports?

- **Local**: Vite dev server with hot reload (development mode)
- Desktop and Mobile run on separate ports for isolation
