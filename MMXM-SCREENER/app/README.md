# MMXM Crypto Screener

This repository contains the backend API and frontend UI for the MMXM crypto screener.

## Local Development Setup

### Backend
1. Open a terminal and go to `app/backend`
2. Activate your Python environment:
   - Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
   - cmd: `\.venv\Scripts\activate.bat`
   - macOS/Linux: `source .venv/bin/activate`
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Copy `backend/.env.example` to `backend/.env` and update any values as needed.
5. Ensure MongoDB is installed and running locally on the configured host/port (default: `mongodb://localhost:27017`).
   - If you have MongoDB installed, start it with `mongod --dbpath <your-db-folder>` or from the MongoDB service manager.
   - If you do not have MongoDB installed, install MongoDB Community Server or use a hosted MongoDB URI and update `app/backend/.env`.
6. Start the backend:
   - `uvicorn server:app --reload --host 0.0.0.0 --port 8000`
7. Verify the backend is working:
   - Open `http://localhost:8000/api/health`

### Frontend
1. Open a terminal and go to `app/frontend`
2. Install dependencies:
   - `yarn install`
   - or `npm install`
3. Copy `frontend/.env.example` to `frontend/.env`.
4. Make sure `REACT_APP_BACKEND_URL` points to your local backend, for example:
   - `REACT_APP_BACKEND_URL=http://localhost:8000`
5. Start the frontend:
   - `yarn start`
   - or `npm start`
6. Open the app in your browser at `http://localhost:3000`

## Notes
- Backend configuration is loaded from `app/backend/.env` by default.
- The frontend uses `REACT_APP_BACKEND_URL` from `app/frontend/.env` to connect to the backend.
- If Mongo is unavailable or the required env vars are missing, the backend will fail fast with a clear error message.

## Docker Compose for MongoDB + Backend

If you have Docker installed, you can start MongoDB, the backend, and the frontend together with Docker Compose.

From the repository root:

```bash
cd "c:\Users\Lenovo\Documents\msmm screener"
docker compose up -d --build
```

This builds and starts the services in detached mode.

To check service status:

```bash
docker compose ps
```

To view logs:

```bash
docker compose logs -f
```

This will launch:

- `mongo` on `mongodb://localhost:27017`
- `backend` on `http://localhost:8000`
- `frontend` on `http://localhost:3001`

The compose setup uses `app/backend/Dockerfile` and `app/frontend/Dockerfile`, and loads backend env vars from `app/backend/.env`.

To stop the services:

```bash
docker compose down
```

If you change environment variables in `app/backend/.env`, restart the stack with:

```bash
docker compose down
 docker compose up -d --build
```
