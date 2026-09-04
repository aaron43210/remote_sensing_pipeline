# =============================================================
# OWNER: AARON
# =============================================================
"""
Physics-Guided Lightweight Neural Network — Fusion Model

This model does NOT calculate physics features itself.
It receives pre-computed parameters from the domain services:
    - spectral_analysis  → NDVI, red edge, chlorophyll depth, water depth, brightness
    - rtm_inversion      → Chlorophyll (Cab), LAI, Water content (Cw), Structure (N)
    - mineral_analysis   → top mineral abundance, confidence, spectral angle
    - thermal            → mean LST, UHI intensity, n_hotspots (optional)

Architecture:
    Fused Features (15-dim from all services)
            ↓
    MLP: Linear(15 → 32) → ReLU → Dropout → Linear(32 → 16) → ReLU → Linear(16 → n_classes)
            ↓
    class_logits → Softmax → Probability Map

Total parameters: ~2,000
CPU inference: <0.1ms per pixel

References:
    - Verrelst et al. (2015): "Machine learning regression
      algorithms for biophysical parameter retrieval."
    - Camps-Valls et al. (2018): "Physics-aware Gaussian processes
      in remote sensing." IEEE TGRS.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ── Feature dimensions from each domain service ─────────────────────────────
# These match what each service publishes to Kafka/MinIO

SPECTRAL_FEATURES = 6   # NDVI, red_edge_pos, red_edge_slope, chl_depth, water_depth, brightness
RTM_FEATURES      = 4   # Chlorophyll, LAI, Water, Structure
MINERAL_FEATURES  = 3   # top_abundance, confidence, spectral_angle
THERMAL_FEATURES  = 3   # mean_LST, UHI_intensity, n_hotspots (optional)

# Total when all services contribute
TOTAL_FEATURES_ALL = SPECTRAL_FEATURES + RTM_FEATURES + MINERAL_FEATURES + THERMAL_FEATURES  # 16
# Without thermal (HSI-only mode)
TOTAL_FEATURES_HSI = SPECTRAL_FEATURES + RTM_FEATURES + MINERAL_FEATURES  # 13


class FusionLightweightNet(nn.Module):
    """
    Lightweight fusion neural network.

    Takes pre-computed parameters from domain services and predicts
    a probability distribution over land cover / material classes.

    This is a simple MLP — no CNN, no convolutions.
    Physics is handled upstream by each domain service.
    """

    def __init__(self, n_features=TOTAL_FEATURES_HSI, n_classes=16):
        super().__init__()

        self.n_features = n_features
        self.n_classes  = n_classes

        # ── Fusion MLP ───────────────────────────────────────────────────
        self.fusion = nn.Sequential(
            nn.Linear(n_features, 32),
            nn.ReLU(),
            nn.BatchNorm1d(32),
            nn.Dropout(0.2),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, n_classes),
        )

        # ── Parameter prediction head (optional: predict physical params) ─
        # Predicts biophysical parameters as auxiliary output
        # This encourages the model to learn physics-consistent features
        self.param_head = nn.Sequential(
            nn.Linear(n_features, 16),
            nn.ReLU(),
            nn.Linear(16, 4),  # Cab, Cw, LAI, N
        )

        total = sum(p.numel() for p in self.parameters() if p.requires_grad)
        logger.info(f"FusionLightweightNet initialized: {total:,} parameters")

    def forward(self, fused_features, return_params=False):
        """
        Forward pass.

        Args:
            fused_features: (batch, n_features) pre-computed feature vector
                            from domain services.
            return_params:  If True, also return predicted physical parameters.

        Returns:
            class_logits: (batch, n_classes) raw logits for classification.
            params:       (batch, 4) predicted physical parameters (optional).
        """
        class_logits = self.fusion(fused_features)

        if return_params:
            params = self.param_head(fused_features)
            return class_logits, params

        return class_logits

    def predict_proba(self, fused_features):
        """
        Get probability distribution over classes.

        Args:
            fused_features: (batch, n_features)

        Returns:
            probabilities: (batch, n_classes) softmax probabilities.
        """
        self.eval()
        with torch.no_grad():
            logits = self.forward(fused_features)
            return F.softmax(logits, dim=1)


class PhysicsConsistencyLoss(nn.Module):
    """
    Loss with physics consistency constraint.

    L = L_class + λ × L_physics

    L_class:   CrossEntropyLoss on classification
    L_physics: MSE between predicted biophysical params and
               the actual values computed by rtm_inversion service

    The physics loss forces the model to learn representations
    that are consistent with real-world physical parameters.
    """

    def __init__(self, lambda_physics=0.1):
        super().__init__()
        self.lambda_physics = lambda_physics
        self.ce_loss = nn.CrossEntropyLoss()

    def forward(self, class_logits, predicted_params, targets,
                true_params=None):
        """
        Args:
            class_logits:    (batch, n_classes) model output logits.
            predicted_params: (batch, 4) model's predicted Cab, Cw, LAI, N.
            targets:         (batch,) ground truth class labels.
            true_params:     (batch, 4) actual params from rtm_inversion.
                             If None, only classification loss is used.

        Returns:
            total_loss: scalar
            details:    dict with component losses
        """
        loss_class = self.ce_loss(class_logits, targets)

        if true_params is not None:
            loss_physics = F.mse_loss(predicted_params, true_params)
            total = loss_class + self.lambda_physics * loss_physics
            return total, {
                'class':   loss_class.item(),
                'physics': loss_physics.item(),
                'total':   total.item(),
            }
        else:
            return loss_class, {
                'class': loss_class.item(),
                'total': loss_class.item(),
            }


def fuse_service_outputs(spectral: dict = None,
                         rtm: dict = None,
                         mineral: dict = None,
                         thermal: dict = None,
                         rows: int = 0, cols: int = 0) -> np.ndarray:
    """
    Fuse outputs from domain services into a single feature array.

    Each service saves its results as .npz / .npy files in MinIO.
    This function loads and concatenates them into a pixel-level
    feature matrix suitable for the neural network.

    Args:
        spectral: dict with keys: ndvi, red_edge, chlorophyll, water (2D arrays)
        rtm:      dict with keys: chlorophyll, lai, water, uncertainty (2D arrays)
        mineral:  dict with keys: abundances (top mineral), confidence, angle_map (2D arrays)
        thermal:  dict with keys: mean_lst, uhi_intensity, n_hotspots (scalars or 2D)
        rows, cols: scene dimensions

    Returns:
        fused: (rows * cols, n_features) float32 array
    """
    feature_maps = []

    # ── Spectral features (6) ────────────────────────────────────────────
    if spectral is not None:
        feature_maps.extend([
            spectral.get('ndvi',       np.zeros((rows, cols))),
            spectral.get('red_edge',   np.zeros((rows, cols))),
            spectral.get('chlorophyll', np.zeros((rows, cols))),
            spectral.get('water',      np.zeros((rows, cols))),
            spectral.get('brightness', np.zeros((rows, cols))),
            # Red edge slope (if available, else zeros)
            spectral.get('red_edge_slope', np.zeros((rows, cols))),
        ])
    else:
        feature_maps.extend([np.zeros((rows, cols))] * SPECTRAL_FEATURES)

    # ── RTM features (4) ─────────────────────────────────────────────────
    if rtm is not None:
        feature_maps.extend([
            rtm.get('chlorophyll', np.zeros((rows, cols))),
            rtm.get('lai',        np.zeros((rows, cols))),
            rtm.get('water',      np.zeros((rows, cols))),
            # Structure or uncertainty
            rtm.get('uncertainty', np.zeros((rows, cols))),
        ])
    else:
        feature_maps.extend([np.zeros((rows, cols))] * RTM_FEATURES)

    # ── Mineral features (3) ─────────────────────────────────────────────
    if mineral is not None:
        # Top mineral abundance (max abundance across all minerals)
        abundances = mineral.get('abundances', {})
        if isinstance(abundances, dict) and len(abundances) > 0:
            # Stack all mineral abundance maps, take max per pixel
            stacked = np.stack(list(abundances.values()), axis=0)
            top_abundance = np.max(stacked, axis=0)
        else:
            top_abundance = np.zeros((rows, cols))

        feature_maps.extend([
            top_abundance,
            mineral.get('confidence', np.zeros((rows, cols))),
            mineral.get('angle_map',  np.zeros((rows, cols))),
        ])
    else:
        feature_maps.extend([np.zeros((rows, cols))] * MINERAL_FEATURES)

    # ── Thermal features (3, optional) ───────────────────────────────────
    if thermal is not None:
        mean_lst = thermal.get('mean_lst', np.zeros((rows, cols)))
        # If thermal gives scalars, broadcast to full image
        if np.isscalar(mean_lst):
            mean_lst = np.full((rows, cols), mean_lst)

        uhi = thermal.get('uhi_intensity', 0.0)
        if np.isscalar(uhi):
            uhi = np.full((rows, cols), uhi)

        hotspots = thermal.get('n_hotspots', 0)
        if np.isscalar(hotspots):
            hotspots = np.full((rows, cols), hotspots)

        feature_maps.extend([mean_lst, uhi, hotspots])

    # ── Stack and reshape ────────────────────────────────────────────────
    # Each map is (rows, cols). Stack → (n_features, rows, cols)
    stacked = np.stack(feature_maps, axis=0).astype(np.float32)
    # Reshape to (rows * cols, n_features)
    n_features = stacked.shape[0]
    fused = stacked.reshape(n_features, -1).T  # (pixels, features)

    # Replace NaN with 0
    fused = np.nan_to_num(fused, nan=0.0)

    logger.info(
        "Fused features: shape=%s, n_features=%d",
        fused.shape, n_features,
    )

    return fused
