# 2026-03-13 - Developing Data Exchange Pipelines (continued)

## Recording

[Recording](https://youtu.be/pHTRYm4yy5w)

## Topics

[Developing Data Exchange Pipelines - Extracting Materials](https://docs.nvidia.com/learn-openusd/latest/data-exchange/data-extraction/exercise-extracting-materials.html)





### Smooth Meshes :  Crease Angles 

Jan had a question how to control the smoothness of meshes (the sphere) vs a faceted appearance (the cone in the example)

In OpenUSD, crease angles are generally managed through mesh topology and crease attributes on a `UsdGeomMesh` rather than a single "crease angle" threshold value. Creases in OpenUSD are defined by identifying specific edges and applying sharpness values, often used for subdivision surface modeling.

 

Key details regarding OpenUSD crease angle and edge sharpness:

- **Crease Attributes:** `UsdGeomMesh` uses `CreateCreaseIndicesAttr` and `CreateCreaseLengthsAttr` to define which edges are creased. `CreateCreaseSharpnessesAttr` defines how sharp those creases are.
- **Crease Sharpness:** Crease sharpness values can be per-crease or per-edge. An infinite value (or a very high number) usually results in a sharp, non-smoothed edge when subdivided, while lower values produce smoother, bevelled edges.
- **Auto Crease Workflow:** While not a native single USD attribute, auto-creasing workflows are used in USD-compatible tools (like [Blender](https://www.blender.org/)/Omniverse) to apply creasing to edges based on the angle between faces, often referred to as "auto edge crease weights".
- **Dihedral Angle:** In some USD-based extensions, such as the Omniverse scene-optimizer, the "crease angle" is treated as the absolute dihedral angle at an edge, above which the edge is deemed sharp. 

For subdivision meshes, crease sharpness is crucial for controlling smooth and hard edge transitions.

#### OpenUSD Documentation:

https://openusd.org/24.08/api/class_usd_geom_mesh.html#ad66811480cf8d706d825c31276684109





