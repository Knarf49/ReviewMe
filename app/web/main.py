from fastapi import FastAPI

from app.web.controllers.auth import router as auth_router
from app.web.controllers.suggestions import router as suggestions_router
from app.web.services.auth.exceptions import register_auth_error_handler

app = FastAPI()
register_auth_error_handler(app)
app.include_router(auth_router)
app.include_router(suggestions_router)


@app.get("/")
def read_root():
    return {"Hello": "World"}
