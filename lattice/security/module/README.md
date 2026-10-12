# lattice-module: security (attack)

A **custom** module (spike 1b) that provides the `attack` subcommand: a spec-driven security
attack swarm run against the OWNER'S OWN app, which the module starts itself on a loopback
throwaway database. It is a **separate module**: not part of `lattice check`, never an A-X
layer, never a rebuild gate.

Install into a project (spike 2's installer):

    lattice module install lattice/security/module --project DIR --yes
    lattice module list --project DIR          # shows `security 0.1.0 custom ok ... provides attack`

Run:

    lattice attack plan --project DIR
    lattice attack run  --project DIR --scope system --classes authz,auth,bounds,mass,state,leak

## Manifest field added for this module

`kind = "custom"` modules may set `entry = "pkg.module"` (the Python module whose `main(argv)`
the host runs for the subcommand(s) in `provides`). This is the one descriptor-schema change
spike 1b needed over spike 2 (which built only the `engine` kind). The entry here is the
in-repo package `lattice.security`; the dispatch from an installed custom module's entry to a
subcommand is the documented-not-fully-wired piece (as the spike-2 layer-A hook is).
