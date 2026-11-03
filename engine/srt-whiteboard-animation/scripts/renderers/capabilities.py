"""Renderer capability registry for deterministic selector rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RendererCapability:
    renderer: str
    capabilities: frozenset[str]


DEFAULT_RENDERER = "whiteboard"

CAPABILITY_PRIORITY = (
    (
        frozenset({"math", "formula", "graph", "geometry", "physics", "scientific_animation"}),
        "manim",
    ),
    (
        frozenset({"infographic", "text", "diagram", "shape", "data", "simple_animation"}),
        "svg",
    ),
    (
        frozenset({"story", "illustration", "hand_drawn", "historical", "character", "scene"}),
        "whiteboard",
    ),
)
