import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import LabelCanvas from "../src/components/LabelCanvas";
import SourcePanel from "../src/components/SourcePanel";
import type { Annotation, DatasetImportResult, Frame, Project, Source } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Plates",
  created_at: "2026-11-25T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const imported: Source = {
  id: "s-8",
  project_id: "p-1",
  type: "dataset",
  path_or_uri: "dataset_anpr_camera8_2026-11-06_2026-11-12.zip",
  fps: 0,
  width: 1920,
  height: 1080,
  frame_count: 272,
  duration_ms: 0,
  created_at: "2026-11-25T00:00:00+00:00",
  is_processing: false,
  is_frozen: false,
  ground_truth_vehicle_count: null,
  stored_frames: 272,
};

function result(source: Source, overrides: Partial<DatasetImportResult> = {}): DatasetImportResult {
  return {
    source,
    images_imported: 272,
    boxes_imported: 285,
    already_imported: 0,
    unreadable: 0,
    repeated_spot_frames: 26,
    low_confidence_frames: 40,
    class_mapping: { "1": "number_plate" },
    ...overrides,
  };
}

function zip(name: string): File {
  return new File(["PK"], name, { type: "application/zip" });
}

function renderPanel() {
  return render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
}

describe("SourcePanel: importing a production dataset", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listSources").mockResolvedValue([]);
  });

  it("uploads each chosen zip and says what to check first", async () => {
    const upload = vi.spyOn(api, "uploadDataset").mockImplementation(async (_p, file) =>
      result({ ...imported, path_or_uri: file.name }),
    );
    renderPanel();

    fireEvent.change(screen.getByTestId("dataset-files"), {
      target: { files: [zip("camera8.zip"), zip("camera1.zip")] },
    });

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(upload.mock.calls.map(([, file]) => file.name)).toEqual(["camera8.zip", "camera1.zip"]);
    expect(await screen.findByText(/camera8\.zip: 272 image\(s\), 285 box\(es\)/)).toBeInTheDocument();
    expect(screen.getByText(/26 with a box where boxes sit in many pictures - check these first/)).toBeInTheDocument();
  });

  it("keeps going past one that fails, and names it", async () => {
    vi.spyOn(api, "uploadDataset").mockImplementation(async (_p, file) => {
      if (file.name === "camera1.zip") {
        throw new ApiError(409, "dataset_already_imported", "Every image in camera1.zip is already in this project");
      }
      return result({ ...imported, path_or_uri: file.name });
    });
    renderPanel();

    fireEvent.change(screen.getByTestId("dataset-files"), {
      target: { files: [zip("camera1.zip"), zip("camera8.zip")] },
    });

    expect(await screen.findByText(/camera1\.zip: Every image in camera1\.zip is already/)).toBeInTheDocument();
    expect(screen.getByText(/camera8\.zip: 272 image\(s\)/)).toBeInTheDocument();
  });

  it("imports a dataset already on the server by its path", async () => {
    const importDataset = vi.spyOn(api, "importDataset").mockResolvedValue(result(imported));
    renderPanel();

    fireEvent.change(screen.getByLabelText("Dataset path on the server"), {
      target: { value: "/incoming/dataset_anpr_camera8.zip" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import dataset" }));

    await waitFor(() => expect(importDataset).toHaveBeenCalledWith("p-1", "/incoming/dataset_anpr_camera8.zip"));
  });

  it("shows an imported dataset as images, with nothing that needs a video", async () => {
    vi.spyOn(api, "listSources").mockResolvedValue([imported]);
    renderPanel();

    expect(await screen.findByText("imported dataset · 272 image(s) · 1920x1080")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /watch/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /detect \+ track/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /freeze as validation clip/i })).not.toBeInTheDocument();
  });
});

describe("SourcePanel: choosing files on this computer", () => {
  const video: Source = { ...imported, id: "s-v", type: "video", path_or_uri: "/workspace/p-1/source/gate.mp4", fps: 25 };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listSources").mockResolvedValue([]);
  });

  it("opens the file picker from a button, for a video and for a dataset", () => {
    renderPanel();

    const videoPicker = vi.spyOn(screen.getByTestId("video-files"), "click");
    fireEvent.click(screen.getByRole("button", { name: /choose video file/i }));
    expect(videoPicker).toHaveBeenCalled();

    const zipPicker = vi.spyOn(screen.getByTestId("dataset-files"), "click");
    fireEvent.click(screen.getByRole("button", { name: /choose \.zip file/i }));
    expect(zipPicker).toHaveBeenCalled();
  });

  it("uploads a chosen video and shows how far the upload has got", async () => {
    let finish: (source: Source) => void = () => {};
    const uploadSource = vi.spyOn(api, "uploadSource").mockImplementation((_p, _file, onProgress) => {
      onProgress?.(512 * 1024 * 1024, 2048 * 1024 * 1024);
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    renderPanel();

    fireEvent.change(screen.getByTestId("video-files"), {
      target: { files: [new File(["x"], "gate.mp4", { type: "video/mp4" })] },
    });

    expect(await screen.findByText(/Uploading gate\.mp4 - 25% \(512 MB of 2\.0 GB\)/)).toBeInTheDocument();
    expect(uploadSource.mock.calls[0][1].name).toBe("gate.mp4");
    expect(screen.getByRole("button", { name: /choose video file/i })).toBeDisabled();

    finish(video);
    expect(await screen.findByText(/gate\.mp4: imported/)).toBeInTheDocument();
    expect(screen.queryByText(/Uploading/)).not.toBeInTheDocument();
  });

  it("says why a video upload was refused", async () => {
    vi.spyOn(api, "uploadSource").mockRejectedValue(
      new ApiError(400, "video_probe_failed", "Could not read video: notes.mp4"),
    );
    renderPanel();

    fireEvent.change(screen.getByTestId("video-files"), {
      target: { files: [new File(["x"], "notes.mp4", { type: "video/mp4" })] },
    });

    expect(await screen.findByText(/notes\.mp4: Could not read video/)).toBeInTheDocument();
  });
});

describe("LabelCanvas: an imported frame", () => {
  const frame: Frame = {
    id: "f-1",
    source_id: "s-8",
    frame_index: 0,
    timestamp_ms: 0,
    width: 1920,
    height: 1080,
    status: "pending",
    selection_reason: "repeated_spot",
  };

  function modelBox(source: string): Annotation {
    return {
      id: `a-${source}`,
      frame_id: "f-1",
      frame_candidate_id: null,
      source,
      class_id: 2,
      bbox_json: [242, 522, 288, 543],
      attributes: {},
      confidence: 0.29,
      status: source === "model" ? "pending" : "accepted",
      updated_at: "2026-11-25T00:00:00+00:00",
    };
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue([{ id: 2, name: "number_plate" }]);
    vi.spyOn(api, "getFrameSuggestions").mockResolvedValue([]);
  });

  it("says its boxes are still the model's until saved", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([modelBox("model")]);

    render(<LabelCanvas project={project} frame={frame} />);

    expect(await screen.findByTestId("label-box")).toBeInTheDocument();
    expect(await screen.findByText(/1 from the model/i)).toBeInTheDocument();
  });

  it("stops saying so once a person has saved them", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([modelBox("human")]);

    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findByTestId("label-box");
    expect(screen.queryByText(/from the model/i)).not.toBeInTheDocument();
  });
});
