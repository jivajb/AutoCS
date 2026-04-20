"""Loads mock customer data from JSON and seeds the store."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.storage.store import Store

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).parent / "mock_customers.json"


def load_mock_data(store: Store) -> int:
    """
    Read mock_customers.json and load every account into the store.
    Returns the number of accounts loaded.
    """
    if not _DATA_FILE.exists():
        logger.warning("Mock data file not found at %s", _DATA_FILE)
        return 0

    with _DATA_FILE.open() as fh:
        accounts = json.load(fh)

    store.load_accounts(accounts)
    logger.info("Loaded %d mock accounts", len(accounts))
    return len(accounts)
