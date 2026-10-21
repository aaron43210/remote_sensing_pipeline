"""
HydraNet: one network, three input stems, a shared trunk, three heads.

    x_agri (22)     -> AgriStem    -+
    x_mineral (530) -> MineralStem -+-> + task embedding -> shared trunk -> [trunk | stem] -> head
    x_thermal (10)  -> ThermalStem -+

Normalisation, output scaling and softmaxes are inside the graph, so the
exported ONNX file takes raw features and returns map-ready values.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

from hydra import spec

N_SPECTRUM = spec.RAW_SLICE.stop                  # 255 continuum-removed + 255 raw reflectance
SCALAR0 = spec.DEPTH_SLICE.start                   # 7 depths + 12 SAM + brightness follow the spectra
N_SCALAR = spec.N_MINERAL_FEATURES - SCALAR0       # 20
N_SAM = len(spec.MINERALS)
N_STRUCT = 2 * N_SAM + 2                           # sam_structure() width


class Standardize(nn.Module):
    def __init__(self, n):
        super().__init__()
        self.register_buffer("mean", torch.zeros(n))
        self.register_buffer("std", torch.ones(n))

    def set(self, mean, std):
        self.mean.copy_(torch.as_tensor(mean, dtype=torch.float32))
        self.std.copy_(torch.clamp(torch.as_tensor(std, dtype=torch.float32), min=1e-6))

    def forward(self, x):
        return (x - self.mean) / self.std


class ResBlock(nn.Module):
    def __init__(self, w, p=0.05):
        super().__init__()
        self.norm = nn.LayerNorm(w)
        self.fc1, self.fc2 = nn.Linear(w, 2 * w), nn.Linear(2 * w, w)
        self.drop = nn.Dropout(p)

    def forward(self, x):
        return x + self.fc2(self.drop(F.gelu(self.fc1(self.norm(x)))))


def mlp(i, o, h=None):
    h = h or max(i, o)
    return nn.Sequential(nn.Linear(i, h), nn.GELU(), nn.Linear(h, o))


class MineralStem(nn.Module):
    """
    Dense-only (lightweight, CPU-fast) stem: both spectra (continuum-removed shape +
    raw reflectance albedo/slope) through one MLP, the 20 scalars and the SAM
    decision structure through another.
    """

    def __init__(self, w):
        super().__init__()
        self.norm = Standardize(spec.N_MINERAL_FEATURES)
        self.spectrum = nn.Sequential(nn.Linear(N_SPECTRUM, 128), nn.GELU(), nn.Linear(128, 96), nn.GELU())
        self.scalars = mlp(N_SCALAR + N_STRUCT, 64, 64)
        self.out = mlp(96 + 64, w, w)

    @staticmethod
    def sam_structure(x_raw):
        """
        The shape of the SAM decision, computed in-graph from the raw angles:
        each angle minus the smallest (the winner is the zero) and the best
        angle's margin to the 'unknown' threshold. Brightness-free by design.
        """
        sam = x_raw[:, spec.SAM_SLICE]
        best = sam.min(dim=1, keepdim=True).values
        rel = sam - best
        # high-gain copy: resolves near-ties of 1e-4 rad (real quartz vs gypsum pixels sit there)
        return torch.cat([rel * 20.0, torch.clamp(rel * 2000.0, 0.0, 1.0),
                          (best - spec.SAM_MAX_ANGLE) * 20.0,
                          torch.clamp((best - spec.SAM_MAX_ANGLE) * 2000.0, -1.0, 1.0)], dim=1)

    def forward(self, x_raw):
        x = self.norm(x_raw)
        s = self.sam_structure(x_raw)
        h = torch.cat([self.spectrum(x[:, :N_SPECTRUM]), self.scalars(torch.cat([x[:, SCALAR0:], s], dim=1))], dim=1)
        return self.out(h), s


ARCHS = {"mlp": (MineralStem, 128, 2)}


class HydraNet(nn.Module):
    def __init__(self, arch="mlp", w=None, n_blocks=None):
        super().__init__()
        stem_cls, w0, b0 = ARCHS[arch]
        w, n_blocks = w or w0, n_blocks or b0
        self.arch = arch
        n_agri, n_th = len(spec.AGRI_BANDS_NM), len(spec.THERMAL_FEATURES)
        self.agri_norm = Standardize(n_agri)
        self.agri_stem = nn.Sequential(mlp(n_agri, w, 2 * w), nn.GELU(), nn.Linear(w, w))
        self.mineral_stem = stem_cls(w)
        self.thermal_norm = Standardize(n_th)
        self.thermal_stem = nn.Sequential(mlp(n_th, w, w), nn.GELU(), nn.Linear(w, w))
        self.task_embed = nn.Parameter(torch.zeros(3, w))
        self.trunk = nn.Sequential(*[ResBlock(w) for _ in range(n_blocks)], nn.LayerNorm(w))

        n_p = len(spec.AGRI_PARAMS)
        self.agri_head = mlp(2 * w, 2 * n_p, w)               # params + uncertainty
        self.mineral_cls = mlp(2 * w + N_STRUCT, len(spec.MINERAL_CLASSES), w)
        self.mineral_abund = mlp(2 * w, len(spec.MINERALS), w)
        self.mineral_quality = mlp(2 * w, 1, w // 2)
        self.thermal_head = mlp(2 * w, 3 + 3 + 1 + 1, w)      # global(3) uhi(3) hot local
        lo = torch.tensor([spec.AGRI_RANGES[p][0] for p in spec.AGRI_PARAMS])
        hi = torch.tensor([spec.AGRI_RANGES[p][1] for p in spec.AGRI_PARAMS])
        self.register_buffer("agri_lo", lo)
        self.register_buffer("agri_span", hi - lo)
        # learned task-uncertainty weights (Kendall et al. 2018), training only
        self.log_vars = nn.Parameter(torch.zeros(3))

    def _shared(self, h, task):
        h = h + self.task_embed[task]
        return torch.cat([self.trunk(h), h], dim=1)

    # Raw (pre-activation) outputs, used by the training losses
    def agri_raw(self, x):
        return self.agri_head(self._shared(self.agri_stem(self.agri_norm(x)), 0))

    def mineral_raw(self, x):
        h, sam = self.mineral_stem(x)
        z = self._shared(h, 1)
        # the class head also sees the SAM structure directly (skip connection)
        return (self.mineral_cls(torch.cat([z, sam], dim=1)), self.mineral_abund(z),
                self.mineral_quality(z).squeeze(1))

    def thermal_raw(self, x):
        return self.thermal_head(self._shared(self.thermal_stem(self.thermal_norm(x)), 2))

    # Map-ready outputs (what ONNX returns)
    def agri_out(self, x):
        raw = self.agri_raw(x)
        n = len(spec.AGRI_PARAMS)
        params = self.agri_lo + torch.sigmoid(raw[:, :n]) * self.agri_span
        std = F.softplus(raw[:, n:]) * self.agri_span * 0.1
        return params, std

    def mineral_out(self, x):
        cls, ab, q = self.mineral_raw(x)
        return F.softmax(cls, dim=1), F.softmax(ab, dim=1), torch.sigmoid(q)

    def thermal_out(self, x):
        r = self.thermal_raw(x)
        return (F.softmax(r[:, 0:3], dim=1), F.softmax(r[:, 3:6], dim=1),
                torch.sigmoid(r[:, 6]), torch.sigmoid(r[:, 7]))


class HydraExport(nn.Module):
    """ONNX entry point: three inputs (each with its own batch size), nine outputs."""

    OUTPUTS = ("agri_params", "agri_std", "mineral_class_probs", "mineral_abundance",
               "mineral_quality", "thermal_global_probs", "thermal_uhi_probs",
               "thermal_hot_prob", "thermal_local_prob")
    INPUTS = ("x_agri", "x_mineral", "x_thermal")

    def __init__(self, net):
        super().__init__()
        self.net = net

    def forward(self, x_agri, x_mineral, x_thermal):
        return (*self.net.agri_out(x_agri), *self.net.mineral_out(x_mineral),
                *self.net.thermal_out(x_thermal))
