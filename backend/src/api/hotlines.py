"""Public hotlines: only numbers confirmed on an official page, each with the date it was checked."""

from fastapi import APIRouter

from src.hotlines import load_hotlines

router = APIRouter(tags=["hotlines"])


@router.get("/hotlines")
def hotlines() -> dict:
    items = load_hotlines()
    return {
        "on_air": [h.model_dump(mode="json") | {"spoken": h.spoken_numbers} for h in items if h.air and h.verified_on],
        "not_aired": [{"service": h.service, "reason": h.note or "not confirmed on an official page"}
                      for h in items if not (h.air and h.verified_on)],
    }
