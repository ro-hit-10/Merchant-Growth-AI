from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.data.loader import load_data
from app.routes import merchants, agent, actions, audit_log, chat

app = FastAPI(title="Vriddhi API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.data = load_data()

app.include_router(merchants.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(audit_log.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "merchants": len(app.state.data.merchants)}
