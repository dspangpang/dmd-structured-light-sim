# DMD coordinate domains

The renderer is a pinhole projector with a rectangular texture. The adapters
define how logical DMD pixels are represented by that texture.

## Orthogonal DMD

Logical `(col,row)` is the carrier pixel `(u,v)` with the same dimensions.
The mapping is identity and no additional projector roll is applied.

## Diamond-DMD: DLP4500

The initial implementation supports 912 columns and 1140 rows. It preserves
the original adapter's mapping:

```text
Logical pixel (row, col)
  U = 2*col + (row mod 2)
  V = row

Unshifted carrier (plus45)
  u = (U - V)/2
  v = (U + V)/2

Carrier pixel
  x = u - u_min
  y = v - v_min
```

Only valid native centers, where `U-V` is even, correspond to logical pixels.
The native coordinate domain spans 1824×1140; it is not a dense image of
independently addressable mirrors. The carrier is 1481×1482 at zero padding,
contains 1,039,680 mapped mirror samples, and has black unused borders.
Each logical sample maps to one distinct carrier pixel. The paired projector
roll is −45° relative to the unrolled optical basis. Applying just an image
rotation, or just a projector rotation, is not the same mapping.

`dmd_mapping.json` contains the exact forward and inverse homogeneous matrices,
dimensions, orientation and a mapping hash. The source library also preserves
the upstream `minus45` transform; the public configuration uses the tested
`plus45` orientation. Both library transforms have permutation/inverse tests.

Exported K/P matrices address **carrier pixel centers**, with the first center
at `(0,0)`. Do not substitute the logical width or native width into these K
matrices. Convert coordinates explicitly using the mapping. Sensor and texture
edge conventions are documented in [pixel_coordinates.md](pixel_coordinates.md).

This is a discrete geometric representation with ideal pixel support. It is
not an electromagnetic simulation of diffraction, micromirror tilt dynamics,
fill-factor gaps, timing, or device-specific radiometric response.
