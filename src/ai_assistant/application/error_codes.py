"""Códigos de error de las respuestas y del catálogo, independientes de OpenAI.

Los adaptadores de salida traducen los del proveedor a estos; los de entrada los exponen al cliente.
"""

AUTH_REQUIRED = "auth_required"
NOT_ELIGIBLE = "not_eligible"
USAGE_LIMIT = "usage_limit"
MODEL_UNAVAILABLE = "model_unavailable"
PROVIDER_UNAVAILABLE = "provider_unavailable"
INCOMPLETE = "incomplete"
INTERRUPTED = "interrupted"
PROVIDER_ERROR = "provider_error"
INTERNAL_ERROR = "internal_error"  # Fallo del propio backend, no del proveedor.
