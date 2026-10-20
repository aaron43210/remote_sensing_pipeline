"""
ONNX Runtime wrapper for hydra.onnx: the only model code that runs in production.

The ONNX graph has three inputs, each with its own batch size, and runs every
head on every call. A task that is not needed gets a single zero row, which
costs microseconds, so one session serves all three tasks.
"""

import json
import os

import numpy as np
import onnxruntime as ort

from . import spec

INPUTS = ("x_agri", "x_mineral", "x_thermal")
OUTPUTS = ("agri_params", "agri_std", "mineral_class_probs", "mineral_abundance",
           "mineral_quality", "thermal_global_probs", "thermal_uhi_probs",
           "thermal_hot_prob", "thermal_local_prob")
_WIDTH = {"x_agri": len(spec.AGRI_BANDS_NM), "x_mineral": spec.N_MINERAL_FEATURES,
          "x_thermal": len(spec.THERMAL_FEATURES)}
_OWNER = {"agri": "x_agri", "mineral": "x_mineral", "thermal": "x_thermal"}


class SpecMismatch(RuntimeError):
    """The model was trained on features computed differently from this code."""


class HydraRuntime:
    def __init__(self, model_path, threads=None, check_spec=True):
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = int(threads or os.getenv("ORT_THREADS", "0") or 0)
        self.session = ort.InferenceSession(str(model_path), opts,
                                            providers=["CPUExecutionProvider"])
        meta = self.session.get_modelmeta().custom_metadata_map
        self.metadata = {k: (json.loads(v) if v[:1] in "{[" else v) for k, v in meta.items()}
        self.version = self.metadata.get("model_version", "unknown")
        if check_spec and self.metadata.get("feature_spec_hash") != spec.feature_spec_hash():
            raise SpecMismatch(
                f"model {self.version} expects feature spec "
                f"{self.metadata.get('feature_spec_hash')}, code has {spec.feature_spec_hash()}")

    def _run(self, task, x):
        feeds = {name: np.zeros((1, _WIDTH[name]), np.float32) for name in INPUTS}
        feeds[_OWNER[task]] = np.ascontiguousarray(x, dtype=np.float32)
        out = dict(zip(OUTPUTS, self.session.run(list(OUTPUTS), feeds)))
        prefix = "agri" if task == "agri" else task
        return {k: v for k, v in out.items() if k.startswith(prefix)}

    def predict(self, task, x, batch=32768):
        """task in {'agri','mineral','thermal'}; x (n, width) float -> dict of (n, ...) arrays."""
        x = np.asarray(x, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != _WIDTH[_OWNER[task]]:
            raise ValueError(f"{task}: expected (n, {_WIDTH[_OWNER[task]]}), got {x.shape}")
        if x.shape[0] == 0:
            return {}
        parts = [self._run(task, x[i:i + batch]) for i in range(0, x.shape[0], batch)]
        return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
