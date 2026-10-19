"""Mineral training-dataset builder and self-validation for the EMIT cube.

Role split
----------
This package is the *mineral-analysis* side of the project.  Its job is to hand
the neural-network team a **proper, validated training dataset**, not to design
the final network.  Before any hand-off we prove the dataset is sound two ways:

1. Normal method  : run the project's own SAM + SMA + quality pipeline (main.py)
                    on the real EMIT image to produce reference mineral maps and
                    per-pixel labels.
2. NN self-check  : train a lightweight neural network on the exported dataset
                    and test it on held-out pixels, then compare its predictions
                    against the SAM/SMA reference.  High agreement == the dataset
                    is learnable and internally consistent, so it is safe to give
                    to the NN designer team.

Modules
-------
library_setup : load the USGS library, align it to the EMIT good-band grid.
features      : deterministic reflectance -> feature-vector transform.
build_dataset : assemble features + SAM/SMA labels into an ML-ready dataset.
validate_nn   : train/test a small MLP on the dataset and score it vs SAM/SMA.
run           : end-to-end driver (cube -> normal method -> dataset -> validate).

Outputs land in  <service>/output/dataset/  (arrays, CSV sample, schema, report).
"""

__all__ = ["library_setup", "features", "build_dataset", "validate_nn"]
