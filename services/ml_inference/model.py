# =============================================================
# OWNER: AARON
# =============================================================
"""
Hydra Multi-Task Learning (MTL) Neural Network

This model operates directly on pre-selected hyperspectral bands
(e.g., 10 target wavelengths). It uses a shared feature extraction
trunk and splits into specialized heads for different domains.

Architecture:
    Input Bands (10-dim)
            ↓
    Shared Trunk (Linear → ReLU → BatchNorm → Dropout)
            ↓
    ├── Agriculture Head (Regression: Cab, Cw, LAI, N)
    ├── Mineral Head     (Softmax: Mineral class probabilities)
    └── Anomaly Head     (Sigmoid: Thermal/fire anomaly probability)

Total parameters: ~3,000
CPU inference: <0.1ms per pixel
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import logging

logger = logging.getLogger(__name__)

INPUT_BANDS = 10
MINERAL_CLASSES = 6  # Example: Background, Kaolinite, Calcite, Illite, Hematite, Montmorillonite

class HydraMTLNet(nn.Module):
    """
    Hydra Multi-Task Learning Network.
    Takes N raw reflectance bands and predicts multi-domain outputs.
    """
    def __init__(self, n_features=INPUT_BANDS, n_mineral_classes=MINERAL_CLASSES):
        super().__init__()

        self.n_features = n_features
        self.n_mineral_classes = n_mineral_classes

        # ── Shared Trunk ─────────────────────────────────────────────────
        self.shared_trunk = nn.Sequential(
            nn.Linear(n_features, 64),
            nn.ReLU(),
            nn.BatchNorm1d(64),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU()
        )

        # ── Agriculture Head (Regression) ────────────────────────────────
        # Predicts biophysical parameters: Cab, Cw, LAI, N
        self.agriculture_head = nn.Sequential(
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, 4)
        )

        # ── Mineral Head (Classification) ────────────────────────────────
        # Predicts abundance probabilities for mineral classes
        self.mineral_head = nn.Sequential(
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, n_mineral_classes)
        )

        # ── Anomaly Head (Binary Classification) ─────────────────────────
        # Predicts probability of thermal/fire anomaly
        self.anomaly_head = nn.Sequential(
            nn.Linear(32, 8),
            nn.ReLU(),
            nn.Linear(8, 1)
        )

        total = sum(p.numel() for p in self.parameters() if p.requires_grad)
        logger.info(f"HydraMTLNet initialized: {total:,} parameters")

    def forward(self, x):
        """
        Forward pass.

        Args:
            x: (batch, n_features) pre-selected reflectance bands

        Returns:
            dict containing outputs from all heads
        """
        shared_features = self.shared_trunk(x)

        agri_params = self.agriculture_head(shared_features)
        mineral_logits = self.mineral_head(shared_features)
        anomaly_logits = self.anomaly_head(shared_features)

        return {
            'agriculture_params': agri_params,
            'mineral_logits': mineral_logits,
            'anomaly_logits': anomaly_logits
        }

    def predict(self, x):
        """
        Get probabilities and concrete predictions for inference.
        """
        self.eval()
        with torch.no_grad():
            outputs = self.forward(x)
            return {
                'agriculture_params': outputs['agriculture_params'],
                'mineral_probs': F.softmax(outputs['mineral_logits'], dim=1),
                'anomaly_prob': torch.sigmoid(outputs['anomaly_logits'])
            }


class HydraMultiLoss(nn.Module):
    """
    Combined Loss for Multi-Task Learning.
    L = w1*L_agri + w2*L_mineral + w3*L_anomaly
    """
    def __init__(self, w_agri=1.0, w_mineral=1.0, w_anomaly=1.0):
        super().__init__()
        self.w_agri = w_agri
        self.w_mineral = w_mineral
        self.w_anomaly = w_anomaly

        self.mse_loss = nn.MSELoss()
        self.ce_loss = nn.CrossEntropyLoss()
        self.bce_loss = nn.BCEWithLogitsLoss()

    def forward(self, outputs, targets):
        """
        Args:
            outputs: dict from HydraMTLNet.forward()
            targets: dict containing ground truth:
                     'agri': (batch, 4)
                     'mineral': (batch,) class indices
                     'anomaly': (batch, 1) binary labels
        """
        loss_agri = self.mse_loss(outputs['agriculture_params'], targets['agri'])
        loss_mineral = self.ce_loss(outputs['mineral_logits'], targets['mineral'])
        loss_anomaly = self.bce_loss(outputs['anomaly_logits'], targets['anomaly'])

        total_loss = (self.w_agri * loss_agri) + \
                     (self.w_mineral * loss_mineral) + \
                     (self.w_anomaly * loss_anomaly)

        return total_loss, {
            'agri': loss_agri.item(),
            'mineral': loss_mineral.item(),
            'anomaly': loss_anomaly.item(),
            'total': total_loss.item()
        }

