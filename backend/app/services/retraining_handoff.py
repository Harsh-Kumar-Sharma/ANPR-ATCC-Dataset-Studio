from pathlib import Path


def write_retraining_handoff(
    export_dir: Path, class_names: list[str], base_model: str, val_split: str = "val"
) -> dict[str, str]:
    """Generate the two files an external training job actually needs
    to pick up an exported dataset version: a standard Ultralytics
    ``data.yaml`` and a short instructions file with the exact command
    to run. This app hands the data off - it does not run training
    itself (a real training run is long-lived and resource-heavy, and
    squarely out of scope for a dataset/review tool).

    ``class_names`` must be the classes the dataset was *exported* with,
    in index order - not the project's current list. The label files on
    disk hold indices assigned at export time, so naming them from a
    since-edited list would mislabel every box.

    ``val_split`` names which split validation reads. Normally "val",
    but a small dataset can split to an empty validation set, and
    training refuses to start without one - so the caller may point it
    at another split rather than leaving the file unusable.
    """
    names_block = "\n".join(f"  {i}: {name}" for i, name in enumerate(class_names))
    data_yaml = (
        f"path: {export_dir.resolve().as_posix()}\n"
        "train: images/train\n"
        f"val: images/{val_split}\n"
        "test: images/test\n"
        f"nc: {len(class_names)}\n"
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


def portable_data_yaml(class_names: list[str], val_split: str = "val") -> str:
    """A ``data.yaml`` for a dataset that has been taken somewhere else.

    No ``path:`` line, deliberately. Ultralytics falls back to the
    directory the yaml itself sits in when the key is absent, so this
    works wherever it is unzipped - which the local one, with this
    machine's absolute path baked into it, does not.
    """
    names_block = "\n".join(f"  {i}: {name}" for i, name in enumerate(class_names))
    return (
        "# Paths are relative to this file, so this works wherever you unzip it.\n"
        "train: images/train\n"
        f"val: images/{val_split}\n"
        "test: images/test\n"
        f"nc: {len(class_names)}\n"
        "names:\n"
        f"{names_block}\n"
    )


def portable_instructions(base_model: str) -> str:
    """What to do with the archive on the machine it lands on.

    Written for somebody holding the zip and nothing else, not for
    somebody sitting in front of this app.
    """
    return (
        "# Training this dataset elsewhere\n\n"
        "Exported by ANPR-ATCC Dataset Studio, in YOLO format, ready to train.\n\n"
        "## What is here\n\n"
        "    images/train  images/val  images/test\n"
        "    labels/train  labels/val  labels/test\n"
        "    data.yaml     classes.txt     manifest.json\n\n"
        "Labels are YOLO text files: one line per box, `class cx cy w h`, normalised.\n"
        "`manifest.json` records exactly which frames and boxes went in, and the split seed.\n\n"
        "## Train\n\n"
        "    pip install ultralytics\n"
        f"    yolo detect train data=data.yaml model={base_model} epochs=100 imgsz=640\n\n"
        "## Notes\n\n"
        "- Unzip anywhere. There are no absolute paths in `data.yaml`.\n"
        "- Swap `model=` for a checkpoint of your own to carry on training rather than\n"
        "  starting from the pretrained base.\n"
        "- This dataset version is immutable, so this command trains on exactly the same\n"
        "  data however long from now you run it.\n"
    )
