"""
Where the serving model comes from: object storage, never the image.

    models/hydra/active.json            {"version": "v1"}   (written by scripts/promote_model.py)
    models/hydra/<v>/hydra.onnx         the network
    models/hydra/<v>/agri_lut.npz       the classical LUT, for shadow verification
    models/hydra/<v>/model_card.json

The service polls active.json and swaps to a newly promoted model between
messages, so a promotion needs no restart and no downtime.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from hs_common import storage
from hs_common.storage import keys
from hydra.classical.agri_lut import LUTInverter
from hydra.runtime import HydraRuntime

log = logging.getLogger(__name__)
CACHE = Path(os.getenv("MODEL_CACHE_DIR", "/tmp/models"))
POLL_S = int(os.getenv("MODEL_POLL_S", "60"))


class ModelStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.version = None
        self.runtime = None
        self.lut = None
        self.card = {}

    def _active_version(self):
        override = os.getenv("MODEL_VERSION")
        if override:
            return override
        try:
            return storage.get_json("models/hydra/active.json")["version"]
        except Exception:
            return None

    def _load(self, version):
        d = CACHE / version
        d.mkdir(parents=True, exist_ok=True)
        for name in ("hydra.onnx", "agri_lut.npz", "model_card.json"):
            if not (d / name).exists():
                storage.get_file(keys.model(version, name), d / name)
        runtime = HydraRuntime(d / "hydra.onnx")          # raises SpecMismatch on a bad model
        lut = LUTInverter.load(d / "agri_lut.npz")
        card = json.loads((d / "model_card.json").read_text())
        with self._lock:
            self.version, self.runtime, self.lut, self.card = version, runtime, lut, card
        log.info("serving HydraNet %s", version)

    def wait_until_ready(self):
        while self.runtime is None:
            v = self._active_version()
            if v:
                try:
                    self._load(v)
                    break
                except Exception:
                    log.exception("could not load model %s", v)
            log.warning("no promoted model yet (models/hydra/active.json); retrying in 15 s")
            time.sleep(15)
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while True:
            time.sleep(POLL_S)
            v = self._active_version()
            if v and v != self.version:
                try:
                    self._load(v)
                except Exception:
                    log.exception("new model %s failed to load; still serving %s", v, self.version)

    def current(self):
        with self._lock:
            return self.version, self.runtime, self.lut
