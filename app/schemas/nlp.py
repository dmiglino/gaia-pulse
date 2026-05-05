from datetime import datetime
from typing import Any

from pydantic import BaseModel


class NLPParseRequest(BaseModel):
    text: str
    input_type: str = "text"  # text / audio


class NLPConfirmRequest(BaseModel):
    event_id: int
    edited_intents: list[dict[str, Any]] | None = None  # user may edit before confirming


class NLPEventRead(BaseModel):
    id: int
    user_id: int
    input_type: str
    original_input: str
    transcription: str | None
    parsed_intent_json: list[dict[str, Any]] | None
    parse_confidence: float | None
    parser_layer: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
