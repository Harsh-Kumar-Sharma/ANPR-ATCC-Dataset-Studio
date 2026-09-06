from pathlib import Path


def write_retraining_handoff(export_dir: Path, class_schema: list[dict], base_model: str) -> dict[str, str]:
    """Generate the two files an external training job actually needs
    to pick up an exported dataset version: a standard Ultralytics
    ``data.yaml`` and a short instructions file with the exact command
    to run. This app hands the data off - it does not run training
    itself (a real training run is long-lived and resource-heavy, and
    squarely out of scope for a dataset/review tool).
    """
    names_block = "\n".join(f"  {i}: {c['name']}" for i, c in enumerate(class_schema))
    data_yaml = (
        f"path: {export_dir.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        f"nc: {len(class_schema)}\n"
        "names:\n"
        f"{names_block}\n"
    )
    data_yaml_path = export_dir / "data.yaml"
    data_yaml_path.write_text(data_yaml, encoding="utf-8")

    instructions = (
        "# Retraining Handoff\n\n"
        "This dataset was exported by ANPR-ATCC Dataset Studio and is ready for YOLO training.\n\n"
        "## Train\n\n"
        f"    yolo detect train data=data.yaml model={base_model} epochs=100 imgsz=640\n\n"
        "## Notes\n\n"
        "- `data.yaml` paths are relative to this directory.\n"
        "- Swap `model=` for a previously fine-tuned checkpoint to continue training instead of\n"
        "  starting from the pretrained base.\n"
        "- This dataset version is immutable - re-running this command later trains on exactly\n"
        "  the same data unless a new dataset version is exported.\n"
    )
    instructions_path = export_dir / "RETRAINING.md"
    instructions_path.write_text(instructions, encoding="utf-8")

    return {"data_yaml_path": str(data_yaml_path), "instructions_path": str(instructions_path)}
