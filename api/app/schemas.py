from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class Table(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class Chart(BaseModel):
    type: Literal["bar"] = "bar"
    x: str
    y: list[str]
    title: str = ""


class Query(BaseModel):
    source: str
    sql: str


class AskResponse(BaseModel):
    answer: str
    table: Table | None = None
    chart: Chart | None = None
    queries: list[Query] = []


class SpeechToken(BaseModel):
    token: str
    region: str
