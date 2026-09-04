# =============================================================
# OWNER: AARON
# =============================================================
"""
CNN baseline for benchmarking comparison.

1D CNN for spectral classification.
"""

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import time
import logging
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

logger = logging.getLogger(__name__)


class CNN1D(nn.Module):
    """Standard 1D CNN baseline."""

    def __init__(self, n_bands, n_classes):
        super().__init__()

        self.conv1 = nn.Conv1d(1, 64, kernel_size=7, padding=3)
        self.bn1 = nn.BatchNorm1d(64)
        self.pool1 = nn.MaxPool1d(2)

        self.conv2 = nn.Conv1d(64, 128, kernel_size=5, padding=2)
        self.bn2 = nn.BatchNorm1d(128)
        self.pool2 = nn.MaxPool1d(2)

        self.conv3 = nn.Conv1d(128, 256, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm1d(256)

        self.gap = nn.AdaptiveAvgPool1d(1)

        self.fc1 = nn.Linear(256, 128)
        self.dropout = nn.Dropout(0.5)
        self.fc2 = nn.Linear(128, n_classes)

    def forward(self, x):
        x = x.unsqueeze(1)
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.pool1(x)
        x = F.relu(self.bn2(self.conv2(x)))
        x = self.pool2(x)
        x = F.relu(self.bn3(self.conv3(x)))
        x = self.gap(x).squeeze(-1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)


class CNNBaseline:
    """Benchmark wrapper for CNN baseline."""

    def __init__(self, n_bands, n_classes, device='cpu'):
        self.device = device
        self.model = CNN1D(n_bands, n_classes).to(device)
        self.n_bands = n_bands

    def train(self, X_train, y_train, X_val=None, y_val=None, epochs=100):
        train_loader = DataLoader(
            TensorDataset(
                torch.FloatTensor(X_train),
                torch.LongTensor(y_train)
            ),
            batch_size=64, shuffle=True
        )

        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(
            self.model.parameters(), lr=0.001, weight_decay=1e-4
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, patience=5, factor=0.5
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
                output = self.model(spectra)
                loss = criterion(output, targets)
                loss.backward()
                optimizer.step()

                total_loss += loss.item()
                pred = output.argmax(dim=1)
                correct += pred.eq(targets).sum().item()
                total += targets.size(0)

            train_acc = correct / total

            if X_val is not None:
                val_acc = self._quick_eval(X_val, y_val)
                scheduler.step(1 - val_acc)

                if epoch % 10 == 0:
                    logger.info(
                        f"CNN Epoch {epoch+1}/{epochs} | "
                        f"Loss={total_loss/len(train_loader):.4f} | "
                        f"Train={train_acc*100:.2f}% | "
                        f"Val={val_acc*100:.2f}%"
                    )

                if val_acc > best_acc:
                    best_acc = val_acc
                    torch.save(self.model.state_dict(), 'best_cnn.pth')

        try:
            self.model.load_state_dict(torch.load('best_cnn.pth'))
        except:
            pass

        return best_acc

    def _quick_eval(self, X, y):
        self.model.eval()
        with torch.no_grad():
            tensor = torch.FloatTensor(X).to(self.device)
            output = self.model(tensor)
            preds = output.argmax(dim=1).cpu().numpy()
        return accuracy_score(y, preds)

    def evaluate(self, X_test, y_test):
        self.model.eval()
        tensor = torch.FloatTensor(X_test).to(self.device)

        start = time.time()
        with torch.no_grad():
            output = self.model(tensor)
            preds = output.argmax(dim=1).cpu().numpy()
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
