from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

ResultT = TypeVar("ResultT", bound=BaseModel)


class ModelSuccess(BaseModel, Generic[ResultT]):
    model: str
    page: int
    status: Literal["ok"] = "ok"
    result: ResultT


class ModelFailure(BaseModel):
    model: str
    page: int
    status: Literal["error"] = "error"
    parse_error: str
    raw_content: str | None
