# Importing each strategy module registers it (see base.py). Add new
# strategies here so they're picked up by the API/engine without touching
# any other file.
from app.strategy import sp2l  # noqa: F401
