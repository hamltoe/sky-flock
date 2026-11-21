"""Detections produced by a detector plugin."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Detection(BaseModel):
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)
    score: float = 1.0
    class_label: str = "light"
