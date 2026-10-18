---
name: ovrtx-video-textures
description: "Use a video file (.mp4) as a texture on OpenUSD surfaces and render it - in Omniverse Kit 110.4 or headless with NVIDIA ovrtx to a PNG, MP4 or GIF. Use for 'play a video on a screen in USD', 'video texture', 'render an animation with a video texture', or an ovrtx render that hangs on a scene with an .mp4, or whose video runs out of step with the animation."
license: Apache-2.0
metadata:
  author: "SyncTwin GmbH"
  tags:
    - openusd
    - ovrtx
    - omniverse
    - video-texture
---

# Video textures with OpenUSD, Kit 110.4 and ovrtx

An `.mp4` bound to a material input like any image texture plays on the surface. Kit 110.4 needs nothing else. A
standalone ovrtx renderer needs two more things: the GStreamer plugins on Windows, and a renderer clock that follows
the animation. This folder is a working example of all three; read `README.md` beside this file for the details.

| File | Use it for |
| --- | --- |
| `video_studio.usda` | A known-good scene: copy its `VideoMaterial`, or test a setup against it |
| `textures/test_video.mp4` | 10 s, 1280 × 720, 30 fps, with a timecode: shows which video frame is on screen |
| `render_video.py` | Render a `.png`, `.mp4` or `.gif` with ovrtx; reuse `ensure_gstreamer` and `frames` |

## 1. Author the material

Put the video where an image would go, on an OmniPBR shader:

```usda
asset inputs:diffuse_texture = @./textures/clip.mp4@ (
    colorSpace = "auto"
)
bool inputs:enable_emission = 1
asset inputs:emissive_color_texture = @./textures/clip.mp4@ (
    colorSpace = "auto"
)
float inputs:emissive_intensity = 1000
```

- Diffuse alone looks like a printed poster. Add the emissive input for a screen that glows like a display.
- The surface needs UVs (`primvars:st`). An implicit `UsdGeom.Sphere`, `Cube` or `Cylinder` has none: use a `Mesh`.
- Give a flat screen the video's aspect ratio (16:9 for 1280 × 720), so the picture is not stretched.
- Set `endTimeCode / timeCodesPerSecond` to the clip length, so the timeline loops with the video and a render with
  no time range covers the whole clip.

In Kit 110.4 open the scene in an RTX viewport and press Play. Nothing else is needed.

## 2. Render with ovrtx

```powershell
uv sync
uv run python render_video.py <scene.usda> <camera prim path> -o out.mp4 --fps 30 --start 0 --end 10
```

`.png` renders one still at `--start`; `.gif` and `.mp4` render `--start` to `--end` (seconds) at `--fps`. The camera
must exist in the scene. In Git Bash, prefix with `MSYS_NO_PATHCONV=1` so the `/World/...` path is not rewritten.

### Windows: the GStreamer plugins

ovrtx loads GStreamer plugins only from `ovrtx/bin/plugins/gpu.foundation/gstreamer-1.0`. The Windows wheel does
not ship that folder, and **without it the first `renderer.step()` on a scene with a video texture blocks forever**,
with no error. Check for the folder before creating the renderer; never debug a hang on Windows before checking it.

- Source: Kit's `omni.gpu_foundation` extension, `<kit>/extscache/omni.gpu_foundation-*/bin/deps/gstreamer-1.0`.
- Pass it as `--gstreamer <folder>` or `GSTREAMER_PLUGINS`; `ensure_gstreamer` copies it into ovrtx before
  `ovrtx.Renderer()` is created.
- `GST_PLUGIN_PATH` and `PATH` do not work: ovrtx restricts the DLL search path, so the plugins' dependencies fail.
- Reinstalling ovrtx (`uv sync`, `pip install`) removes the copy: copy on every start.
- Windows will not load a plugin whose path is over 260 characters, and the render hangs again: keep the venv short.
- The folder holds NVIDIA proprietary DLLs from Kit. Do not commit it to a public repository.

Linux wheels ship the plugins; nothing to do there.

### Keep the video in step with the animation

Video textures play on ovrtx's simulation clock, which every `step(delta_time=...)` advances, not on USD time.
`ovstage.population.update_from_usd_time` moves the stage, not the video. With warm-up steps of 1/60 s per frame the
video runs ahead (8 steps per frame at 30 fps = 4× too fast). Drive the clock:

1. `renderer.reset(t0)` with the first frame's time in seconds (not below 0).
2. On each later frame's first step, `delta_time` = the gap since the previous frame.
3. Every other step, warm-up and capture alike: `delta_time=0`.

`frames()` in `render_video.py` does this; reuse it rather than rewriting it.

### ovrtx 0.6 / ovstage 0.3 basics

- Open the scene through a root layer that sublayers it and adds a `RenderProduct` bound to the camera
  (`render_layer`). A layer opened from a string has no folder: sublayer the scene by absolute path.
- Every stage change goes under a new ordinal, sealed with `stage.advance_write_floor(ordinal).wait()` before a step
  names it; otherwise "ordinal N exceeds ovstage write floor".
- `render_vars` is keyed by the full path, `<product>/LdrColor`. Copy the mapped pixels before the next step.
- The first `ovrtx.Renderer()` compiles shaders for minutes; later starts take seconds.

## 3. Verify

The demo clip shows its own timecode, so a render can be checked against the source:

1. Render `video_studio.usda` from `/World/Studio/Camera`, 640 × 360, as a still at `--start 5`.
2. Crop the screen (pixels 318, 118 to 580, 258), scale it and the source frames to 64 × 36, and find the closest
   source frame by mean squared error.
3. Expect frame 150 (5 s × 30 fps). Within 4 frames is in step: the decoder trails by a frame or so.

| Symptom | Cause | Fix |
| --- | --- | --- |
| First step never returns (Windows) | No GStreamer plugins in ovrtx | Copy Kit's set in (above) |
| Still hangs with the plugins there | Plugin path over 260 characters | Shorter venv path |
| Hangs after a `uv sync` that worked before | Reinstall removed the copy | Copy on every start |
| Video ahead of the animation | Warm-up steps advance the clock | Drive the clock (above) |
| Black or missing texture on a sphere | Implicit prim, no UVs | `Mesh` with `primvars:st` |
| Dull screen in a dark scene | Video only on diffuse | Add the emissive input |
