import json
import asyncio
from typing import Any, Dict, Optional
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType


class JsonFileStorage(BaseStorage):
    def __init__(self, path: str = "/root/projects/wanglogistic/data/fsm_states.json"):
        self._path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {}
        self._loaded = False

    def _key(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    def _load(self):
        if self._loaded:
            return
        try:
            with open(self._path, "r") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}
        self._loaded = True

    def _save(self):
        import os
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f)

    async def set_state(self, key: StorageKey, state: StateType = None):
        async with self._lock:
            self._load()
            k = self._key(key)
            if k not in self._data:
                self._data[k] = {}
            self._data[k]["state"] = state.state if state else None
            self._save()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with self._lock:
            self._load()
            return self._data.get(self._key(key), {}).get("state")

    async def set_data(self, key: StorageKey, data: Dict[str, Any]):
        async with self._lock:
            self._load()
            k = self._key(key)
            if k not in self._data:
                self._data[k] = {}
            self._data[k]["data"] = data
            self._save()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        async with self._lock:
            self._load()
            return dict(self._data.get(self._key(key), {}).get("data") or {})

    async def close(self):
        pass
