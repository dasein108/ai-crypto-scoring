from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    name: str
    category: str
    access: str
    docs_url: str
    enabled: bool
    notes: str = ""
