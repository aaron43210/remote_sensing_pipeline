---
title: HydraNet Maps
emoji: 🛰️
colorFrom: green
colorTo: blue
sdk: static
pinned: false
short_description: Crop traits, minerals and heat maps from NASA EMIT + Landsat
---

# HydraNet Maps — read-only demo

Real maps made by the full platform from **NASA EMIT** hyperspectral scenes and **Landsat 8/9**
thermal scenes over California: crop traits (chlorophyll, leaf water, leaf area), the dominant
mineral and mineral abundances, and urban heat zones and hotspots. Open a sample under **Jobs**,
switch tabs, hover the map for values. No sign-in: nothing here runs on a server.

Every map was made by **HydraNet v1**, a 502k-parameter, 2.06 MB ONNX network, and checked
pixel by pixel against the classical methods it replaces (SAM + SMA for minerals, PROSAIL
lookup-table inversion for crops, statistical rules for heat).

* Model, metrics and verification: [aaronr43210/HydraNet-Hyperspectral](https://huggingface.co/aaronr43210/HydraNet-Hyperspectral)
* Source (Go gateway, Kafka workers, Python science, React UI, Terraform for AWS): [github.com/aaron43210/remote_sensing_pipeline](https://github.com/aaron43210/remote_sensing_pipeline)

Making new maps for any area needs the full platform (NASA Earthdata download, preprocessing,
inference), which runs on AWS with Google sign-in.
