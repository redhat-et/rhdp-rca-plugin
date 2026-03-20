"""Optional MLflow tracing support. No-ops when mlflow is not installed."""

try:
    import mlflow
    from mlflow.entities import SpanType

    HAS_MLFLOW = True
except ImportError:
    mlflow = None  # noqa: N816
    SpanType = None
    HAS_MLFLOW = False


def trace(name, span_type=None):
    """Decorator that traces with mlflow if available, otherwise no-op."""
    if HAS_MLFLOW:
        return mlflow.trace(name=name, span_type=span_type)
    return lambda fn: fn
