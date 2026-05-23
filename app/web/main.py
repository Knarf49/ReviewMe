from fastapi import FastAPI

from controllers.auth import router as auth_router
from services.auth.exceptions import register_auth_error_handler

app = FastAPI()
register_auth_error_handler(app)
app.include_router(auth_router)


@app.get("/")
def read_root():
    return {"Hello": "World"}
