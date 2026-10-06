"""
Standalone app for the PO Assemblies page: only the /po-assemblies endpoints, without the OCR
stack, so it builds and starts in seconds.

Run: uvicorn po_app:app --host 0.0.0.0 --port $PORT
Env: GLIDE_API_KEY, GLIDE_APP_ID
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import po_assemblies

app = FastAPI(title="PO Assemblies")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://wootz-work.github.io", "http://localhost:3000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(po_assemblies.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
