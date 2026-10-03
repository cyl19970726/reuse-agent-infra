"""Synthetic example: the bookshelf's one table. Its structure must not change (the user's rule)."""
from dataclasses import dataclass


@dataclass
class Book:
    isbn: str
    title: str
    author: str
    added_at: str
