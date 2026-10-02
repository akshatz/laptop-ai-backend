from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from routers import auth, chats

app = FastAPI(title="Laptop Dev Backend Engine", default_response_class=ORJSONResponse)

# 🖥️ Minimal static chat UI for exercising /chats + /chats/{chat_id}/messages
# by hand, at http://localhost:8011/ui/
app.mount("/ui", StaticFiles(directory="static", html=True), name="ui")

app.include_router(auth.router)
app.include_router(chats.router)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": "laptop-sandbox"}
