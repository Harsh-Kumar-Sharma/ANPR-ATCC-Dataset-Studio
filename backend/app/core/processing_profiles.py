"""Named processing profile presets.

docs/06_UI_UX_SPEC.md Screen 2 (Source Import) requires a processing
profile selector. Profiles are lightweight presets over
``sampling_config`` - not a persisted table, since
docs/05_DATABASE_DESIGN.md stores the chosen config as JSON on
``processing_runs`` rather than as a separate schema entity.
"""

DEFAULT_PROCESSING_PROFILES: dict[str, dict] = {
    "fast": {"label": "Fast", "target_fps": 2.0},
    "balanced": {"label": "Balanced", "target_fps": 5.0},
    "high_recall": {"label": "High Recall", "target_fps": 10.0},
}
