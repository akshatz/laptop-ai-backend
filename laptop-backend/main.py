from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from routers import admin, auth, chats, sso

app = FastAPI(title="Laptop Dev Backend Engine", default_response_class=ORJSONResponse)

# API only: the chat UI is the separate laptop-frontend app.
app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(sso.router)
app.include_router(admin.router)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": "laptop-sandbox"}
