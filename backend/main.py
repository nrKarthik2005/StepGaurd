"""FastAPI application. Run with:  uvicorn backend.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes import VERSION, router
from backend.config import ensure_dirs
from backend.core.audit_logger import get_logger
from backend.core.workflow_engine import SpecError, load_all
from backend.database.connection import init_db

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    wfs = load_all(refresh=True)          # fail fast if any specification is invalid
    log.info("StepGuard API started with workflows %s", sorted(wfs))
    yield


app = FastAPI(title="StepGuard API", version=VERSION, lifespan=lifespan,
              description="Runtime verification and state-aware recovery for procedural errors in LLM agents. "
                          "All results are produced by real runs of the simulation harness.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.exception_handler(SpecError)
async def spec_error(_: Request, exc: SpecError):
    return JSONResponse(status_code=500, content={"detail": f"workflow specification error: {exc}"})


@app.exception_handler(KeyError)
async def key_error(_: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'\"")})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"internal error: {type(exc).__name__}"})
