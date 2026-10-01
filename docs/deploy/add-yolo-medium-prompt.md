# Prompt: add YOLO26 medium to the built-in models

Copy everything below the line and give it to the agent.

---

Add **YOLO26 medium** as a third built-in model, so the picker offers nano, small
and medium. Everything else about model selection already works — training on a
chosen model, detecting with a chosen model, per-project custom models. Nothing
there needs touching.

This is a small change. Resist making it a big one.

## The change

In `backend/app/ml/models.py`, `BUILTIN_MODELS` currently holds two entries. Add a
third:

```python
BuiltinModel(
    id="yolo26m",
    label="YOLO26 medium",
    weights_file="yolo26m.pt",
    note="...",
),
```

Write the `note` yourself in the voice of the two beside it: they say what the
trade-off *is* for someone choosing, not what the model is. Medium's trade-off is
that it is slower again and needs more GPU memory, in exchange for finding more on
hard footage — small plates, night, rain.

**Order matters twice over:**

- `DEFAULT_MODEL_ID = BUILTIN_MODELS[0].id`, so **nano must stay first** or you have
  silently changed the default model for every new project.
- Put medium **after** small, so the list reads fastest → most accurate. The user
  said "n, m, s"; they meant "all three", and ascending size is the order that
  explains itself in a dropdown.

That is the whole code change. `list_models`, `ensure_weights`, the picker, the
training panel and the per-project filtering all read from this tuple and need
nothing.

## Verify the weights actually download

This is the part that can genuinely fail. Built-in weights are fetched on first use
(`ensure_weights_for` in `backend/app/ml/weights.py`), and a model whose file name
does not exist upstream turns into a download error the first time someone picks it
— after they have waited.

So **do not ship this on the assumption that `yolo26m.pt` exists**. Confirm it:

```
python -c "from ultralytics import YOLO; YOLO('yolo26m.pt')"
```

run somewhere disposable, and check it lands a real file. If the asset is not
published under that name, **stop and report it** — do not substitute a different
model, and do not fall back to a v8/v11 medium to make the menu look complete. A
dropdown entry that fails on click is worse than three honest options.

If it does download, leave the file where the app expects it
(`ANPR_MODEL_WEIGHTS_DIR`, `data/models` by default) so the first real use is
instant rather than a surprise pause. Say in your report how large it is — medium is
several times nano, and this machine shares one disk with MLFF.

## Tests

One existing test pins the list and will fail — that is the test doing its job:

- `backend/tests/test_model_choice.py:42` — `assert ids == ["yolo26n", "yolo26s"]`

Update it to the three ids in the new order. Then **read the rest of that file**:
some tests use `yolo26s` as "the one that is not the default", and those should keep
meaning that.

Add one test of your own: that medium is listed, is `kind="builtin"`, and is **not**
the default. The default changing by accident is the regression worth a test here.

Then run both suites:

- `cd backend && pytest -q` (~874 tests)
- `cd desktop && npm test` (~319 tests)

Both must pass. If the frontend has a fixture listing the built-ins, update it; if
it does not, do not invent one.

## What not to do

- **Do not add v8, v11 or any other family.** Three models that differ along one
  axis is a choice someone can make. A menu of nine is not.
- **Do not change the default.** Nano stays.
- **Do not pre-download medium in the Docker image.** It bloats the image for a
  model most projects will not use, and the fetch-on-first-use path already exists
  and already tells the user it is happening.
- **Do not touch the deployment work** if it is in progress. This is independent of
  it; keep it in its own commit.

## Report back

- The note text you wrote for medium.
- Whether `yolo26m.pt` downloaded, and how large it is.
- Which test you updated and which you added.
- Both suite results.
