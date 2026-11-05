"""Store-floor replenishment: forecast tonight's shelf demand and draft a pick list."""

import os

# The offline path never exports traces. Set this before the agent SDK is imported.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

__version__ = "0.1.0"
