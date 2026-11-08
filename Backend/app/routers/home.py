from fastapi import APIRouter

router = APIRouter(tags=["home"])


@router.get("/home")
def home() -> dict:
    """Landing-page content. Public. Replaces the old Flask `/api/home` stub."""
    return {
        # Same copy as the frontend's fallback in Frontend/src/pages/Home.tsx.
        "mission": ["Agentic Outreach for", "Federal Agencies."],
        "buttons": ["Start a communication", "View agents"],
    }
