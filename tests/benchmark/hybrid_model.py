# =============================================================
# OWNER: AARON
# =============================================================
"""
Hybrid physics-guided lightweight NN for benchmarking.
"""

import os
import numpy as np
import torch
import time
import logging
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

logger = logging.getLogger(__name__)


class HybridBenchmark:
    """
    Benchmark wrapper for PhysicsGuidedLightweightNet.
    """

    def __init__(self, n_bands, n_classes, device='cpu'):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/ml_inference'))
        from model import PhysicsGuidedLightweightNet

        self.device = device
        self.model = PhysicsGuidedLightweightNet(
            n_bands=n_bands,
            n_classes=n_classes,
            embedding_dim=32
        ).to(device)

        self.n_bands = n_bands
        self.n_classes = n_classes

    def train(self, X_train, y_train, X_val=None, y_val=None, epochs=50):
        """Train hybrid model."""
        from model import PhysicsConsistencyLoss

        train_loader = DataLoader(
            TensorDataset(
                torch.FloatTensor(X_train),
                torch.LongTensor(y_train)
            ),
            batch_size=32, shuffle=True
        )

        criterion = PhysicsConsistencyLoss(lambda_physics=0.1)
        optimizer = torch.optim.Adam(
            self.model.parameters(), lr=0.001, weight_decay=1e-4
        )

        best_acc = 0

        for epoch in range(epochs):
            self.model.train()
            total_loss = 0
            correct = 0
            total = 0

            for spectra, targets in train_loader:
                spectra = spectra.to(self.device)
                targets = targets.to(self.device)

                optimizer.zero_grad()
                logits, params = self.model(spectra)
                loss, _ = criterion(logits, params, targets, spectra)
                loss.backward()
                optimizer.step()

                total_loss += loss.item()
                pred = logits.argmax(dim=1)
                correct += pred.eq(targets).sum().item()
                total += targets.size(0)

            train_acc = correct / total

            if X_val is not None and epoch % 10 == 0:
                val_acc = self._quick_eval(X_val, y_val)
                logger.info(
                    f"Epoch {epoch+1}/{epochs} | "
                    f"Loss={total_loss/len(train_loader):.4f} | "
                    f"Train Acc={train_acc*100:.2f}% | "
                    f"Val Acc={val_acc*100:.2f}%"
                )
                if val_acc > best_acc:
                    best_acc = val_acc
                    torch.save(self.model.state_dict(), 'best_hybrid.pth')
            elif epoch % 10 == 0:
                logger.info(
                    f"Epoch {epoch+1}/{epochs} | "
                    f"Loss={total_loss/len(train_loader):.4f} | "
                    f"Train Acc={train_acc*100:.2f}%"
                )

        # Load best
        try:
            self.model.load_state_dict(torch.load('best_hybrid.pth'))
        except:
            pass

        return best_acc

    def _quick_eval(self, X, y):
        self.model.eval()
        with torch.no_grad():
            tensor = torch.FloatTensor(X).to(self.device)
            logits, _ = self.model(tensor)
            preds = logits.argmax(dim=1).cpu().numpy()
        return accuracy_score(y, preds)

    def evaluate(self, X_test, y_test):
        """Full evaluation with timing."""
        self.model.eval()
        tensor = torch.FloatTensor(X_test).to(self.device)

        start = time.time()
        with torch.no_grad():
            logits, params = self.model(tensor)
            preds = logits.argmax(dim=1).cpu().numpy()
            param_vals = params.cpu().numpy()
        inference_time = time.time() - start

        acc = accuracy_score(y_test, preds)
        f1 = f1_score(y_test, preds, average='weighted')
        cm = confusion_matrix(y_test, preds)

        return {
            'accuracy': acc,
            'f1_score': f1,
            'confusion_matrix': cm,
            'inference_time': inference_time,
            'time_per_sample_ms': (inference_time / len(X_test)) * 1000,
            'throughput': len(X_test) / inference_time,
            'n_parameters': sum(p.numel() for p in self.model.parameters()),
            'model_size_kb': sum(
                p.numel() for p in self.model.parameters()
            ) * 4 / 1024
        }
