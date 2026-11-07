from fastapi import APIRouter

router = APIRouter(tags=["home"])


@router.get("/home")
def home() -> dict:
    """Landing-page content. Public. Replaces the old Flask `/api/home` stub."""
    return {
        "mission": ["Mission", "statement."],
        "buttons": ["button1", "button2"],
    }
