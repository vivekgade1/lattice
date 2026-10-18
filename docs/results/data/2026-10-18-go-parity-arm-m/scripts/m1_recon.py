"""M1 reconnaissance (NOT the registered command): what the rules-first import can derive for Streak
before the seed step that needs a database. Mirrors lattice.importer.rules.import_rules up to candidate_spec."""
import json, sys
from pathlib import Path
import lattice
from lattice.importer import go_rules
from lattice.importer.emit_spec import candidate_spec
from lattice.importer.frontends import select
from lattice.importer.lift import lift_repo
from lattice.importer.rules import Seeder, _runtime
root = Path(sys.argv[1]).resolve(); package = "."
sys.path.insert(0, str(root))
fe = select("gin", root, package)
fe = go_rules.InferringFrontEnd(fe)
factory, name = go_rules.harness_factory(root, package)
model = fe.extract(root, package)
runtime = _runtime(fe, root, package, model)
result = lift_repo(root, package, factory, only=(), frontend=fe, runtime=runtime)
go_rules.patch_profile(result, fe.private, fe.validation_status)
ctx = result.context; api = result.lattice_profile.api
seeder = Seeder(result.model, ctx.inputs.tables, ctx.inputs.auth, ctx.inputs.insert_hook, runtime, factory, api.uuid_storage, api.datetime_storage)
users = seeder.credentials()
spec = candidate_spec(result, users=users)
out = {
 "lattice": lattice.__file__, "factory": name, "front_end": fe.name,
 "routes": len(result.model.routes), "tables": [t.name for t in result.model.tables],
 "dialect": result.model.dialect, "auth_users_table": result.model.auth.users_table if result.model.auth else None,
 "contracts": len(spec.contracts), "programs": len(spec.programs), "scenarios": len(spec.scenarios),
 "inferences": list(getattr(fe, "inferences", ())),
 "validation_status": api.validation_status,
 "ownership": dict(result.profile.ownership), "ownership_reasons": dict(result.profile.ownership_reasons),
 "excluded": [(e.method, e.path, e.kind) for e in result.model.excluded_routes],
 "seed_users": sorted(users),
}
print(json.dumps(out, indent=1, default=str))
