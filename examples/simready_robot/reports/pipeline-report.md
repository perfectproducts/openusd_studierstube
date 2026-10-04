# CAD to SimReady report: kr270r2700ultra

**Overall status:** `blocked`

- Source: `testfiles\kr270r2700ultra.jt`
- Profile: `Prop-Robotics-Neutral@1.0.0`
- Converted USD: `output\kr270r2700ultra\pipeline\02_convert\usd\kr270r2700ultra.usd`
- Physics USD: `output\kr270r2700ultra\pipeline\04_assign\physics\kr270r2700ultra_material_physics.usd`
- Final (conformed) USD: `output\kr270r2700ultra\pipeline\07_conform_rerun\fet004-multibody\kr270r2700ultra_material_physics.usd`
- Render: `output\kr270r2700ultra\pipeline\09_render\thumbnail.png`

## Stages

| Stage | Status | Report |
|---|---|---|
| preflight | ready | `output\kr270r2700ultra\pipeline\00_preflight\cad-to-simready-preflight.json` |
| context | passed | `output\kr270r2700ultra\pipeline\01_context\asset-context.json` |
| convert | passed | `output\kr270r2700ultra\pipeline\02_convert\conversion.json` |
| minimum | passed | `output\kr270r2700ultra\pipeline\03_minimum\minimum-usd.json` |
| assign | PASS | `output\kr270r2700ultra\pipeline\04_assign\content-agents.json` |
| conform | PASS | `output\kr270r2700ultra\pipeline\05_conform\simready-conform-profile.json` |
| validate | needs_rerun | `` |
| conform_rerun | BLOCKED | `output\kr270r2700ultra\pipeline\07_conform_rerun\simready-conform-profile.json` |
| validate_rerun | needs_rerun | `` |
| render | passed | `output\kr270r2700ultra\pipeline\09_render\ovrtx-render-service.json` |

## Validation gates

| Gate | Passed | Failing requirements |
|---|---|---|
| asset | False | - |
| geometry | True | - |
| physics | True | - |
| simready | False | GSP.001 |

## Rerun reasons

- blocked: Joint Agent produced no rigged package (no accepted joint candidates?): HTTP Error 404: Not Found
- asset: failed
- simready: GSP.001
- FET005 (GSP.001) blocked: GSP.001 needs visually selected grasp points; render the asset, pick points and rerun with --grasp-point x,y,z --from conform
