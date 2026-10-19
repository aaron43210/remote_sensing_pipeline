"""Central settings for the Neural Network (student) side of the mineral-analysis
service. USGS/SAM/SMA are used only offline, to generate training labels; the
trained model never calls them at inference. Edit the values here."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- Paths (each can be overridden by an environment variable) ---
TRAIN_DATA_PATH = Path(os.getenv("NN_TRAIN_DATA", BASE_DIR / "data" / "nn" / "train.npz"))
VAL_DATA_PATH = Path(os.getenv("NN_VAL_DATA", BASE_DIR / "data" / "nn" / "val.npz"))
TEST_DATA_PATH = Path(os.getenv("NN_TEST_DATA", BASE_DIR / "data" / "nn" / "test.npz"))
MODEL_DIR = Path(os.getenv("NN_MODEL_DIR", BASE_DIR / "models"))
LOG_DIR = Path(os.getenv("NN_LOG_DIR", BASE_DIR / "logs"))
OUTPUT_MAP_DIR = Path(os.getenv("NN_OUTPUT_DIR", BASE_DIR / "output_nn"))

MODEL_FILENAME = "spectral_transformer.pt"
TRAINING_LOG_FILENAME = "training_log.csv"

# --- Minerals: the class list, set once per project, not hardcoded in model code ---
# The 12 USGS library endmembers (matches output/training_set/ class_names order).
MINERAL_NAMES = ["alunite", "calcite", "chlorite", "dolomite", "goethite", "gypsum",
                 "hematite", "illite", "kaolinite", "montmorillonite", "muscovite", "quartz"]
NUM_CLASSES = len(MINERAL_NAMES)

# --- Input spectrum ---
# Number of wavelengths the model expects per spectrum. Must match the wavelength
# grid produced by wavelength_alignment.py for the training data; not a satellite-
# specific or arbitrary band count.
NUM_WAVELENGTHS = None

# --- 1D spectral Transformer ---
EMBED_DIM = 64
NUM_HEADS = 4
NUM_LAYERS = 4
FEEDFORWARD_DIM = 256
DROPOUT = 0.1
# Abundance head output constraint; "softmax" gives non-negative values summing to 1.
ABUNDANCE_ACTIVATION = "softmax"

# --- Training ---
LEARNING_RATE = 1e-4
WEIGHT_DECAY = 1e-5
BATCH_SIZE = 64
MAX_EPOCHS = 100
EARLY_STOPPING_PATIENCE = 10
RANDOM_SEED = 42

# Multi-task loss: total = CLASSIFICATION_LOSS_WEIGHT * cls_loss + ABUNDANCE_LOSS_WEIGHT * abundance_loss
CLASSIFICATION_LOSS_WEIGHT = 1.0
ABUNDANCE_LOSS_WEIGHT = 1.0

# --- Train/validation/test split (used only when generating/splitting a dataset
# that is not already split into TRAIN/VAL/TEST_DATA_PATH files) ---
TRAIN_FRACTION = 0.7
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15


def validate():
    """Raise ValueError if a setting is invalid or inconsistent."""
    if NUM_CLASSES < 1 or len(set(MINERAL_NAMES)) != NUM_CLASSES:
        raise ValueError("MINERAL_NAMES must be a non-empty list of unique names")
    if NUM_WAVELENGTHS is not None and NUM_WAVELENGTHS < 1:
        raise ValueError("NUM_WAVELENGTHS must be positive or None (unset)")
    if EMBED_DIM % NUM_HEADS != 0:
        raise ValueError(f"EMBED_DIM ({EMBED_DIM}) must be divisible by NUM_HEADS ({NUM_HEADS})")
    for name, value in (("NUM_LAYERS", NUM_LAYERS), ("FEEDFORWARD_DIM", FEEDFORWARD_DIM),
                        ("BATCH_SIZE", BATCH_SIZE), ("MAX_EPOCHS", MAX_EPOCHS),
                        ("EARLY_STOPPING_PATIENCE", EARLY_STOPPING_PATIENCE)):
        if value < 1:
            raise ValueError(f"{name} must be >= 1")
    if not (0 <= DROPOUT < 1):
        raise ValueError("DROPOUT must be in [0, 1)")
    if LEARNING_RATE <= 0 or WEIGHT_DECAY < 0:
        raise ValueError("LEARNING_RATE must be positive and WEIGHT_DECAY non-negative")
    if ABUNDANCE_ACTIVATION != "softmax":
        raise ValueError("ABUNDANCE_ACTIVATION must be 'softmax'")
    if CLASSIFICATION_LOSS_WEIGHT < 0 or ABUNDANCE_LOSS_WEIGHT < 0:
        raise ValueError("loss weights must be non-negative")
    fractions = (TRAIN_FRACTION, VAL_FRACTION, TEST_FRACTION)
    if any(f <= 0 for f in fractions) or abs(sum(fractions) - 1.0) > 1e-6:
        raise ValueError("TRAIN_FRACTION + VAL_FRACTION + TEST_FRACTION must equal 1.0")


# --- Runtime config object for nn_main.py -----------------------------------
# nn_main.py expects `nn_config.load_nn_config(path)` to return a single config
# object the whole pipeline reads from. We assemble it from the constants above.
import dataclasses  # noqa: E402

_SERVICE_DIR = BASE_DIR.parents[1]  # .../services/mineral_analysis


@dataclasses.dataclass
class NNConfig:
    # data / labels
    mineral_names: tuple = tuple(MINERAL_NAMES)
    library_dir: str = str(_SERVICE_DIR / "data" / "usgs_library")
    band_metadata: str = str(_SERVICE_DIR / "band_metadata.json")
    apply_continuum_removal: bool = True
    n_pure_per_mineral: int = 200
    n_mixtures: int = 3000
    minerals_per_mixture: tuple = (2, 3)
    # teacher (SAM/SMA); None = never mark "unknown", so every sample keeps a label
    sam_max_angle: float | None = None
    sma_max_rmse: float | None = None
    # model
    d_model: int = EMBED_DIM
    num_heads: int = NUM_HEADS
    num_layers: int = NUM_LAYERS
    ff_dim: int = FEEDFORWARD_DIM
    dropout: float = DROPOUT
    pooling: str = "mean"
    patch_size: int = 1
    # training (max_epochs/batch_size/learning_rate/seed are overridden by nn_main CLI)
    learning_rate: float = LEARNING_RATE
    weight_decay: float = WEIGHT_DECAY
    batch_size: int = BATCH_SIZE
    max_epochs: int = MAX_EPOCHS
    early_stopping_patience: int = EARLY_STOPPING_PATIENCE
    seed: int = RANDOM_SEED
    classification_weight: float = CLASSIFICATION_LOSS_WEIGHT
    abundance_weight: float = ABUNDANCE_LOSS_WEIGHT
    abundance_loss: str = "mse"
    split_fractions: tuple = (TRAIN_FRACTION, VAL_FRACTION, TEST_FRACTION)


def load_nn_config(path=None):
    """Return the pipeline config. `path`, if given, is a JSON file whose keys
    override NNConfig fields (unknown keys are rejected)."""
    validate()
    cfg = NNConfig()
    if path:
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        fields = {f.name for f in dataclasses.fields(cfg)}
        unknown = set(data) - fields
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        cfg = dataclasses.replace(cfg, **data)
    return cfg
