PARAMETER_BOUNDS = {
    "Cab": (0.0, 110.0),
    "Car": (0.0, 30.0),
    "Cw": (0.00005, 0.07),
    "Cm": (0.001, 0.05),
    "LAI": (0.0, 10.0),
    "ALA": (0.0, 90.0),
}


def bound_predictions(predictions):
    """Clip NN predictions to configured physical search bounds."""
    bounded = {}

    for name, value in predictions.items():
        if name not in PARAMETER_BOUNDS:
            raise ValueError(f"Unexpected target parameter: {name}")

        lower, upper = PARAMETER_BOUNDS[name]
        value = float(value)

        if not __import__("math").isfinite(value):
            raise ValueError(f"NN returned a non-finite value for {name}")

        bounded[name] = min(max(value, lower), upper)

    if set(bounded) != set(PARAMETER_BOUNDS):
        raise ValueError("NN must return all six target parameters.")

    return bounded
