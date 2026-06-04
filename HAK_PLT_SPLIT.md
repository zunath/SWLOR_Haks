# swlor2_plt / swlor2_core3 split

PLT assets are split across two hak source folders to stay under the ~2 GB toolset limit.

## Rule (second character of basename)

| 2nd char | Phenotype | Hak folder |
|----------|-----------|------------|
| `m` | Male (`pmh0`, `pmz0`, `ipm_`, …) | `swlor2_plt` |
| `f` | Female (`pfh0`, `pfz0`, `ipf_`, …) | `swlor2_core3` |

Anything that does **not** use `m` or `f` as the second character (e.g. `helm_*`, `cloak_*`) stays in **`swlor2_plt`**.

## Toolset / override

Load **both** folders (or both built `.hak` files). Pointing only at `swlor2_plt` will miss all female parts.

## Adding new PLTs

- Male part → `swlor2_plt/`
- Female part → `swlor2_core3/`
- Rebuild haks after changes (`BuildHaks.cmd` from `SWLOR_Haks` or `Build/` with HakBuilder `-k`).
