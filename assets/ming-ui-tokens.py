#!/usr/bin/env python3
"""Shared Ming Mint visual tokens used by the desktop surfaces."""

TOKENS = {
    "canvas": "#F4F7F3",
    "surface": "#FFFFFF",
    "surface_elevated": "#FBFDFB",
    "surface_subtle": "#EEF5F1",
    "accent": "#2F8A7D",
    "accent_strong": "#1F7668",
    "text": "#1B2320",
    "muted": "#5B6B64",
    "success": "#2E8B68",
    "warning": "#B7791F",
    "danger": "#C24B4B",
    "focus": "#3AAE99",
    "border": "#D7E4DE",
    "shadow": "#17483C",
}


def token(name, fallback=""):
    return TOKENS.get(name, fallback)
