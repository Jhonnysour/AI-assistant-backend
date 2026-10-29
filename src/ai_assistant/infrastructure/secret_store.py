"""Archivo de secretos cifrado en reposo.

En Windows se usa DPAPI (CryptProtectData) ligado a la cuenta del usuario: el archivo no se
puede descifrar desde otra cuenta ni desde otra máquina. El almacenamiento para el VPS Linux
está pendiente (docs/development.md); hasta entonces se rechaza en otras plataformas.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretStoreCorrupted(RuntimeError):
    pass


class DpapiProtector:
    _FLAG_UI_FORBIDDEN = 0x1

    def __init__(self, entropy: bytes) -> None:
        if sys.platform != "win32":
            raise SecretStoreUnavailable("DPAPI solo está disponible en Windows.")
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self._ctypes = ctypes
        self._blob_type = DataBlob
        self._entropy = entropy
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signature = [
            ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
        self._protect = crypt32.CryptProtectData
        self._protect.argtypes = signature
        self._protect.restype = wintypes.BOOL
        self._unprotect = crypt32.CryptUnprotectData
        self._unprotect.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
        self._unprotect.restype = wintypes.BOOL
        self._local_free = kernel32.LocalFree
        self._local_free.argtypes = [ctypes.c_void_p]

    def _blob(self, data: bytes):
        buffer = self._ctypes.create_string_buffer(data, len(data))
        pointer = self._ctypes.cast(buffer, self._ctypes.POINTER(self._ctypes.c_char))
        return self._blob_type(len(data), pointer), buffer

    def _call(self, function, data: bytes, description) -> bytes:
        ctypes = self._ctypes
        data_in, _keep_in = self._blob(data)
        entropy, _keep_entropy = self._blob(self._entropy)
        data_out = self._blob_type()
        ok = function(
            ctypes.byref(data_in), description, ctypes.byref(entropy),
            None, None, self._FLAG_UI_FORBIDDEN, ctypes.byref(data_out),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "Fallo de DPAPI")
        try:
            return ctypes.string_at(data_out.pbData, data_out.cbData)
        finally:
            self._local_free(ctypes.cast(data_out.pbData, ctypes.c_void_p))

    def protect(self, data: bytes) -> bytes:
        return self._call(self._protect, data, "ai-assistant")

    def unprotect(self, data: bytes) -> bytes:
        try:
            return self._call(self._unprotect, data, None)
        except OSError as error:
            raise SecretStoreCorrupted(
                "No se pudo descifrar el archivo de credenciales con esta cuenta de Windows."
            ) from error


class ProtectedFile:
    """Lee y escribe un archivo cifrado; la escritura es atómica."""

    def __init__(self, path: Path, protector: DpapiProtector) -> None:
        self.path = path
        self._protector = protector

    def read(self) -> bytes | None:
        if not self.path.exists():
            return None
        return self._protector.unprotect(self.path.read_bytes())

    def write(self, data: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_bytes(self._protector.protect(data))
        os.replace(temporary, self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def default_protector(entropy: bytes) -> DpapiProtector:
    if sys.platform != "win32":
        raise SecretStoreUnavailable(
            "No hay almacenamiento protegido configurado para esta plataforma. "
            "Ver la sección de despliegue pendiente en docs/development.md."
        )
    return DpapiProtector(entropy)
