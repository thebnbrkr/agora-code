"""
Fixture: sample FastAPI app for extractor tests.
Not meant to be run — only parsed by python_ast extractor.
"""
from fastapi import FastAPI, Query

app = FastAPI()


@app.get("/users/{user_id}")
async def get_user(user_id: int, include_details: bool = False):
    """Fetch a user by ID."""
    pass


@app.post("/users")
async def create_user(name: str, email: str):
    """Create a new user."""
    pass


@app.delete("/users/{user_id}")
async def delete_user(user_id: int):
    """Delete a user by ID."""
    pass


@app.get("/products")
async def list_products(category: str = Query(None), limit: int = 10):
    """List products with optional category filter."""
    pass
