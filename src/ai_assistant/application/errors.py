"""Errores de la aplicación que pueden lanzar los adaptadores de salida y llegan a los de entrada."""


class StorageUnavailable(Exception):
    """El almacenamiento no respondió. El mensaje de la excepción nunca contiene datos del usuario."""

    code = "storage_unavailable"
