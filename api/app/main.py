"""REST API in front of the Foundry Orders agent. Also serves the web front-end."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .agent import OrdersAgent
from .config import settings
from .schemas import AskRequest, AskResponse, SpeechToken

logging.basicConfig(level=logging.INFO)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = await run_in_threadpool(OrdersAgent)
    yield


app = FastAPI(title="Orders Voice Analyst API", version="1.0.0", lifespan=lifespan)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/api/speech-token", response_model=SpeechToken)
async def speech_token():
    """Exchange the Speech key (from Key Vault) for a 10-minute token,
    so the browser never sees the key."""
    url = f"https://{settings.speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, headers={"Ocp-Apim-Subscription-Key": settings.speech_key})
    if r.status_code != 200:
        raise HTTPException(502, "Could not get a speech token.")
    return SpeechToken(token=r.text, region=settings.speech_region)


@app.post("/api/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request):
    try:
        return await run_in_threadpool(request.app.state.agent.ask, body.question)
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    except Exception:
        logging.exception("ask failed")
        raise HTTPException(500, "The agent failed to answer. Please try again.")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
