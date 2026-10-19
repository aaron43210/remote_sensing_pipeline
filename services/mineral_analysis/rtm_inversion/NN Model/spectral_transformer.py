from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import torch
import torch.nn.functional as F
from torch import Tensor, nn

_POOLING = ("cls", "mean")
_INPUT_TRANSFORMS = ("none", "absorption_depth")
_ABUNDANCE_LOSSES = ("kl", "mse")
_SAM_LOSSES = ("mse", "smooth_l1")
_FORMAT_VERSION = 1


@dataclass
class SpectralTransformerConfig:
    num_bands: int
    num_minerals: int
    mineral_names: tuple[str, ...] | None = None
    d_model: int = 128
    num_heads: int = 4
    num_layers: int = 4
    ff_dim: int = 256
    dropout: float = 0.1
    patch_size: int = 1
    pooling: str = "mean"
    input_transform: str = "absorption_depth"
    input_scale: float = 10.0
    position_scale: float = 100.0
    head_hidden_dim: int = 128
    predict_sam: bool = False
    sam_output_dim: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "num_bands",
            "num_minerals",
            "d_model",
            "num_heads",
            "num_layers",
            "ff_dim",
            "patch_size",
            "head_hidden_dim",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.d_model % self.num_heads != 0:
            raise ValueError("d_model must be divisible by num_heads")
        if self.d_model % 2 != 0:
            raise ValueError("d_model must be even")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if self.pooling not in _POOLING:
            raise ValueError(f"pooling must be one of {_POOLING}")
        if self.input_transform not in _INPUT_TRANSFORMS:
            raise ValueError(f"input_transform must be one of {_INPUT_TRANSFORMS}")
        if self.sam_output_dim is not None and self.sam_output_dim < 1:
            raise ValueError("sam_output_dim must be >= 1")
        if self.mineral_names is not None:
            self.mineral_names = tuple(str(m) for m in self.mineral_names)
            if len(self.mineral_names) != self.num_minerals:
                raise ValueError("mineral_names length must equal num_minerals")

    @property
    def sam_dim(self) -> int:
        return self.sam_output_dim or self.num_minerals

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["mineral_names"] is not None:
            data["mineral_names"] = list(data["mineral_names"])
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpectralTransformerConfig":
        return cls(**dict(data))


@dataclass
class SpectralTransformerOutput:
    class_logits: Tensor
    abundance_logits: Tensor
    abundances: Tensor
    features: Tensor
    sam_pred: Tensor | None = None


def _sinusoidal_encoding(positions: Tensor, dim: int) -> Tensor:
    freqs = torch.exp(
        torch.arange(0, dim, 2, dtype=torch.float32) * (-math.log(10000.0) / dim)
    )
    angles = positions.to(torch.float32)[:, None] * freqs[None, :]
    encoding = torch.zeros(positions.shape[0], dim)
    encoding[:, 0::2] = torch.sin(angles)
    encoding[:, 1::2] = torch.cos(angles)
    return encoding


def _mlp_head(in_dim: int, hidden: int, out_dim: int, dropout: float) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(in_dim, hidden),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden, out_dim),
    )


class SpectralTransformer(nn.Module):
    def __init__(self, config: SpectralTransformerConfig, wavelengths) -> None:
        super().__init__()
        self.config = config
        cfg = config

        wl = torch.as_tensor(wavelengths, dtype=torch.float32).detach().clone()
        if wl.shape != (cfg.num_bands,):
            raise ValueError(
                f"wavelengths must have shape ({cfg.num_bands},), got {tuple(wl.shape)}"
            )
        if not torch.isfinite(wl).all() or (wl[1:] <= wl[:-1]).any():
            raise ValueError("wavelengths must be finite and strictly increasing")
        self.register_buffer("wavelengths", wl)

        self.num_tokens = math.ceil(cfg.num_bands / cfg.patch_size)
        self._padded_bands = self.num_tokens * cfg.patch_size
        pad = self._padded_bands - cfg.num_bands
        wl_padded = torch.cat([wl, wl[-1:].expand(pad)]) if pad else wl
        token_wl = wl_padded.view(self.num_tokens, cfg.patch_size).mean(dim=1)
        span = float(wl[-1] - wl[0]) or 1.0
        positions = (token_wl - wl[0]) / span * cfg.position_scale
        self.register_buffer(
            "position_encoding",
            _sinusoidal_encoding(positions, cfg.d_model),
            persistent=False,
        )

        self.embed = nn.Linear(cfg.patch_size, cfg.d_model)
        self.cls_token = (
            nn.Parameter(torch.randn(1, 1, cfg.d_model) * 0.02)
            if cfg.pooling == "cls"
            else None
        )
        self.dropout = nn.Dropout(cfg.dropout)

        layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,
            nhead=cfg.num_heads,
            dim_feedforward=cfg.ff_dim,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer,
            num_layers=cfg.num_layers,
            norm=nn.LayerNorm(cfg.d_model),
            enable_nested_tensor=False,
        )

        self.classifier = _mlp_head(
            cfg.d_model, cfg.head_hidden_dim, cfg.num_minerals, cfg.dropout
        )
        self.abundance_head = _mlp_head(
            cfg.d_model, cfg.head_hidden_dim, cfg.num_minerals, cfg.dropout
        )
        self.sam_head = (
            _mlp_head(cfg.d_model, cfg.head_hidden_dim, cfg.sam_dim, cfg.dropout)
            if cfg.predict_sam
            else None
        )

    def _tokenize(self, x: Tensor) -> Tensor:
        pad = self._padded_bands - x.shape[1]
        if pad:
            x = F.pad(x, (0, pad))
        return x.reshape(x.shape[0], self.num_tokens, self.config.patch_size)

    def forward(self, spectra: Tensor) -> SpectralTransformerOutput:
        cfg = self.config
        if spectra.ndim == 3 and spectra.shape[-1] == 1:
            spectra = spectra.squeeze(-1)
        if spectra.ndim != 2 or spectra.shape[1] != cfg.num_bands:
            raise ValueError(
                f"expected input of shape (B, {cfg.num_bands}), got {tuple(spectra.shape)}"
            )

        x = spectra
        if cfg.input_transform == "absorption_depth":
            x = 1.0 - x
        x = x * cfg.input_scale

        h = self.embed(self._tokenize(x)) + self.position_encoding
        if self.cls_token is not None:
            h = torch.cat([self.cls_token.expand(h.shape[0], -1, -1), h], dim=1)
        h = self.encoder(self.dropout(h))

        features = h[:, 0] if self.cls_token is not None else h.mean(dim=1)
        abundance_logits = self.abundance_head(features)
        return SpectralTransformerOutput(
            class_logits=self.classifier(features),
            abundance_logits=abundance_logits,
            abundances=F.softmax(abundance_logits, dim=-1),
            features=features,
            sam_pred=self.sam_head(features) if self.sam_head is not None else None,
        )

    def num_parameters(self, trainable_only: bool = True) -> int:
        return sum(
            p.numel() for p in self.parameters() if p.requires_grad or not trainable_only
        )

    def save(self, path: str | Path, extra: Mapping[str, Any] | None = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "format_version": _FORMAT_VERSION,
            "config": self.config.to_dict(),
            "state_dict": self.state_dict(),
        }
        if extra:
            payload["extra"] = dict(extra)
        torch.save(payload, path)

    @classmethod
    def load(
        cls, path: str | Path, map_location: str | torch.device = "cpu"
    ) -> "SpectralTransformer":
        # Build on CPU from CPU-mapped tensors (so the wavelengths buffer and the
        # freshly-created parameters share a device), then move to map_location.
        payload = torch.load(path, map_location="cpu", weights_only=True)
        if payload.get("format_version") != _FORMAT_VERSION:
            raise ValueError(
                f"unsupported checkpoint format: {payload.get('format_version')}"
            )
        config = SpectralTransformerConfig.from_dict(payload["config"])
        state = payload["state_dict"]
        model = cls(config, state["wavelengths"])
        model.load_state_dict(state)
        model.to(map_location)
        return model.eval()


@dataclass
class MultiTaskLossConfig:
    classification_weight: float = 1.0
    abundance_weight: float = 1.0
    sam_weight: float = 0.0
    abundance_loss: str = "kl"
    sam_loss: str = "mse"

    def __post_init__(self) -> None:
        for name in ("classification_weight", "abundance_weight", "sam_weight"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.abundance_loss not in _ABUNDANCE_LOSSES:
            raise ValueError(f"abundance_loss must be one of {_ABUNDANCE_LOSSES}")
        if self.sam_loss not in _SAM_LOSSES:
            raise ValueError(f"sam_loss must be one of {_SAM_LOSSES}")


class MultiTaskLoss(nn.Module):
    def __init__(
        self,
        config: MultiTaskLossConfig | None = None,
        class_weights: Tensor | None = None,
    ) -> None:
        super().__init__()
        self.config = config or MultiTaskLossConfig()
        self.register_buffer(
            "class_weights",
            None if class_weights is None else class_weights.to(torch.float32),
        )

    def forward(
        self, output: SpectralTransformerOutput, batch: Mapping[str, Tensor]
    ) -> dict[str, Tensor]:
        cfg = self.config

        target = batch["class_target"]
        if target.is_floating_point():
            classification = F.binary_cross_entropy_with_logits(
                output.class_logits, target
            )
        else:
            classification = F.cross_entropy(
                output.class_logits, target, weight=self.class_weights
            )

        abundance_target = batch["abundance"]
        if cfg.abundance_loss == "kl":
            abundance = F.kl_div(
                F.log_softmax(output.abundance_logits, dim=-1),
                abundance_target,
                reduction="batchmean",
            )
        else:
            abundance = F.mse_loss(output.abundances, abundance_target)

        total = (
            cfg.classification_weight * classification
            + cfg.abundance_weight * abundance
        )
        losses = {"classification": classification, "abundance": abundance}

        if cfg.sam_weight > 0:
            if output.sam_pred is None:
                raise ValueError("sam_weight > 0 but the model has no SAM head")
            if "sam_angle" not in batch:
                raise KeyError("sam_weight > 0 but the batch has no 'sam_angle'")
            sam_target = batch["sam_angle"].reshape(output.sam_pred.shape[0], -1)
            if sam_target.shape != output.sam_pred.shape:
                raise ValueError(
                    f"sam_angle shape {tuple(sam_target.shape)} does not match "
                    f"SAM prediction {tuple(output.sam_pred.shape)}"
                )
            if cfg.sam_loss == "mse":
                sam = F.mse_loss(output.sam_pred, sam_target)
            else:
                sam = F.smooth_l1_loss(output.sam_pred, sam_target)
            total = total + cfg.sam_weight * sam
            losses["sam"] = sam

        losses["total"] = total
        return losses
