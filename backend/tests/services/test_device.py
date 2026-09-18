import logging

import pytest

from app.ml.device import CPU_DEVICE, resolve_device


def _available(*, name: str = "NVIDIA GeForce GTX 1650"):
    return lambda: True, lambda index: name


def _unavailable():
    def name(index: int) -> str:  # pragma: no cover - must never be called
        raise AssertionError("device name queried with no CUDA device present")

    return lambda: False, name


def test_auto_picks_the_first_cuda_device_when_one_is_available():
    cuda_available, device_name = _available()

    assert resolve_device("auto", cuda_available=cuda_available, device_name=device_name) == "cuda:0"


def test_auto_falls_back_to_cpu_when_no_cuda_device_is_present():
    cuda_available, device_name = _unavailable()

    assert resolve_device("auto", cuda_available=cuda_available, device_name=device_name) == CPU_DEVICE


def test_falling_back_to_cpu_warns_loudly_rather_than_failing_silently(caplog):
    cuda_available, device_name = _unavailable()

    with caplog.at_level(logging.WARNING):
        resolve_device("auto", cuda_available=cuda_available, device_name=device_name)

    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_choosing_a_cuda_device_logs_which_one_it_chose(caplog):
    cuda_available, device_name = _available(name="NVIDIA GeForce GTX 1650")

    with caplog.at_level(logging.INFO):
        resolve_device("auto", cuda_available=cuda_available, device_name=device_name)

    assert "GTX 1650" in caplog.text


def test_cpu_is_honoured_even_when_a_gpu_is_available():
    """An explicit cpu preference is an override, not a hint - it is how a
    user works around a driver problem without editing code."""
    cuda_available, device_name = _available()

    assert resolve_device("cpu", cuda_available=cuda_available, device_name=device_name) == CPU_DEVICE


def test_an_explicit_cuda_index_is_preserved():
    cuda_available, device_name = _available()

    assert resolve_device("cuda:1", cuda_available=cuda_available, device_name=device_name) == "cuda:1"


def test_bare_cuda_normalises_to_the_first_device():
    cuda_available, device_name = _available()

    assert resolve_device("cuda", cuda_available=cuda_available, device_name=device_name) == "cuda:0"


def test_an_explicitly_requested_gpu_falls_back_to_cpu_rather_than_crashing(caplog):
    """Asking for CUDA on a machine that has none should degrade, not abort:
    the app still has to open so the user can see why."""
    cuda_available, device_name = _unavailable()

    with caplog.at_level(logging.WARNING):
        resolved = resolve_device("cuda:0", cuda_available=cuda_available, device_name=device_name)

    assert resolved == CPU_DEVICE
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_no_preference_is_treated_as_auto():
    cuda_available, device_name = _available()

    assert resolve_device(None, cuda_available=cuda_available, device_name=device_name) == "cuda:0"


def test_an_unrecognised_preference_is_rejected():
    """Config typos surface at startup rather than silently running on CPU
    for an hour."""
    cuda_available, device_name = _available()

    with pytest.raises(ValueError):
        resolve_device("gpu", cuda_available=cuda_available, device_name=device_name)
