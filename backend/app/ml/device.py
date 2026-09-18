"""Explicit torch device selection.

Nothing in the app used the GPU before this module existed: the
installed torch was a ``+cpu`` build and every model load simply took
whatever device the library happened to default to. That is a bad
default to inherit silently, because the difference between CPU and
CUDA here is the difference between a training run taking hours and
taking days.

So device choice is made once, out loud, and in one place. The rule is
that the app always starts: an unavailable GPU degrades to CPU with a
warning rather than aborting, because a user whose driver broke needs
the app to open far more than they need it to be fast. The one thing
that does abort is an unrecognised preference, which is a config typo
and would otherwise be indistinguishable from "ran on CPU all night".
"""

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

CPU_DEVICE = "cpu"
AUTO = "auto"

CudaAvailable = Callable[[], bool]
DeviceName = Callable[[int], str]


def _torch_cuda_available() -> bool:
    import torch

    return bool(torch.cuda.is_available())


def _torch_device_name(index: int) -> str:
    import torch

    return str(torch.cuda.get_device_name(index))


def _parse_cuda_index(preference: str) -> int:
    """``cuda`` means device 0; ``cuda:N`` means device N."""
    _, _, suffix = preference.partition(":")
    if not suffix:
        return 0
    try:
        return int(suffix)
    except ValueError:
        raise ValueError(f"Not a valid CUDA device: {preference!r}. Expected 'cuda' or 'cuda:<index>'.") from None


def resolve_device(
    preference: str | None = None,
    *,
    cuda_available: CudaAvailable = _torch_cuda_available,
    device_name: DeviceName = _torch_device_name,
) -> str:
    """Resolve a configured device preference to a concrete torch device.

    ``auto`` (the default) takes the GPU when there is one. ``cpu``
    forces CPU even on a GPU machine - an override, not a hint.
    ``cuda`` / ``cuda:N`` asks for a specific device and warns if it has
    to fall back.

    The probes are injected so this is testable on any machine; callers
    should leave them alone.
    """
    normalized = (preference or AUTO).strip().lower()

    if normalized == CPU_DEVICE:
        logger.info("Using device cpu (explicitly configured)")
        return CPU_DEVICE

    if normalized == AUTO:
        if not cuda_available():
            logger.warning(
                "No CUDA device available - falling back to cpu. Detection and training will be "
                "substantially slower. Check the NVIDIA driver and that torch is a CUDA build "
                "(a '+cpu' version string means it is not)."
            )
            return CPU_DEVICE
        return _select_cuda(0, device_name)

    if normalized == "cuda" or normalized.startswith("cuda:"):
        index = _parse_cuda_index(normalized)
        if not cuda_available():
            logger.warning(
                "Device %r was requested but no CUDA device is available - falling back to cpu.",
                preference,
            )
            return CPU_DEVICE
        return _select_cuda(index, device_name)

    raise ValueError(f"Unrecognised device preference: {preference!r}. Expected 'auto', 'cpu', 'cuda' or 'cuda:<index>'.")


def _select_cuda(index: int, device_name: DeviceName) -> str:
    device = f"cuda:{index}"
    try:
        name = device_name(index)
    except Exception:
        # Naming is cosmetic; never worth failing a run over.
        name = "unknown device"
    logger.info("Using device %s (%s)", device, name)
    return device
