"""Mémoire GPU par processus et totale, via les compteurs de performance Windows (PDH).

Les compteurs « GPU Process Memory(pid_*)\\Dedicated Usage » et « GPU Adapter
Memory(*)\\Dedicated Usage » existent pour tout pilote WDDM 2.x (AMD, NVIDIA, Intel) : pas
besoin d'outil du constructeur. Lus par ctypes (pas de typeperf/PowerShell à chaque tick).
Tout échec rend None : l'interface affiche « — ».
"""

from __future__ import annotations

import os
import re
import threading

PROCESS_COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"
ADAPTER_COUNTER = r"\GPU Adapter Memory(*)\Dedicated Usage"
_PID_RE = re.compile(r"pid_(\d+)_", re.IGNORECASE)
_DISPLAY_CLASS = (r"SYSTEM\CurrentControlSet\Control\Class"
                  r"\{4d36e968-e325-11ce-bfc1-08002be10318}")


def parse_process_instances(values: dict[str, int]) -> dict[int, int]:
    """{nom d'instance: octets} → {pid: octets} (un processus peut avoir plusieurs
    instances, une par adaptateur : on additionne)."""
    result: dict[int, int] = {}
    for name, value in values.items():
        match = _PID_RE.search(name or "")
        if match and value:
            pid = int(match.group(1))
            result[pid] = result.get(pid, 0) + int(value)
    return result


def total_vram_bytes() -> int | None:
    """Mémoire dédiée de la plus grosse carte (registre du pilote d'affichage), ou None."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return None
    best = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as root:
            for index in range(64):
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        for value_name in ("HardwareInformation.qwMemorySize",
                                           "HardwareInformation.MemorySize"):
                            try:
                                value, _kind = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            if isinstance(value, bytes):
                                value = int.from_bytes(value[:8], "little")
                            best = max(best, int(value or 0))
                except OSError:
                    continue
    except OSError:
        return None
    return best or None


class GpuCounters:
    """Requête PDH ouverte une fois ; `sample()` → ({pid: octets}, octets utilisés) ou None."""

    PDH_FMT_LARGE = 0x00000400
    PDH_MORE_DATA = 0x800007D2

    def __init__(self):
        self._lock = threading.Lock()
        self._query = None
        self._counters: dict[str, object] = {}
        self.available = False
        self.error = ""
        if os.name != "nt":
            self.error = "Windows seulement"
            return
        try:
            self._open()
            self.available = True
        except Exception as exc:  # compteurs absents (pilote ancien, service désactivé…)
            self.error = str(exc)[:160]

    def _open(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct, self._wt = ctypes, wintypes
        pdh = ctypes.WinDLL("pdh")
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR,
                                              ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.c_void_p]
        pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        for fn in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                   "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
            getattr(pdh, fn).restype = wintypes.DWORD
        self._pdh = pdh

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", Value)]

        self._Item = Item
        query = wintypes.HANDLE()
        status = pdh.PdhOpenQueryW(None, None, ctypes.byref(query))
        if status:
            raise OSError(f"PdhOpenQuery 0x{status:08X}")
        self._query = query
        for name, path in (("process", PROCESS_COUNTER), ("adapter", ADAPTER_COUNTER)):
            counter = wintypes.HANDLE()
            status = pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(counter))
            if status:
                raise OSError(f"PdhAddEnglishCounter {path} 0x{status:08X}")
            self._counters[name] = counter
        pdh.PdhCollectQueryData(query)

    def _array(self, counter) -> dict[str, int]:
        ct, wt = self._ct, self._wt
        size, count = wt.DWORD(0), wt.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(counter, self.PDH_FMT_LARGE,
                                                        ct.byref(size), ct.byref(count), None)
        if status != self.PDH_MORE_DATA or not size.value:
            return {}
        buffer = (ct.c_byte * size.value)()
        status = self._pdh.PdhGetFormattedCounterArrayW(counter, self.PDH_FMT_LARGE,
                                                        ct.byref(size), ct.byref(count), buffer)
        if status:
            return {}
        items = ct.cast(buffer, ct.POINTER(self._Item))
        return {items[i].szName: int(items[i].FmtValue.largeValue)
                for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)}

    def sample(self) -> tuple[dict[int, int], int] | None:
        if not self.available:
            return None
        with self._lock:
            try:
                if self._pdh.PdhCollectQueryData(self._query):
                    return None
                per_pid = parse_process_instances(self._array(self._counters["process"]))
                adapters = self._array(self._counters["adapter"])
                return per_pid, max(adapters.values(), default=0)
            except Exception as exc:
                self.error = str(exc)[:160]
                return None

    def close(self) -> None:
        with self._lock:
            if self._query is not None:
                try:
                    self._pdh.PdhCloseQuery(self._query)
                except Exception:
                    pass
                self._query = None
                self.available = False
