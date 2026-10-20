// DN808 state machines (PY808; only with the policy's `machines`): a status write (B1's N7, `status_writes`) of a state of the
// machine from a guarded state that has no edge to it. The states a write can come from are what the extraction read: a
// dominating refusal (`if (x.Status != A) return 409`) or the `Where` of a bulk update. No guard, a value that is not a state
// of the machine, or a guard state the machine does not name stay silent. Enum members are matched by name or by the stored
// value (`enum_storage`), case-insensitively.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

namespace DnCheck;

public sealed partial class Ex
{
    void CheckMachines(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN808") || pol.Machines.Count == 0) return;
        foreach (var kv in pol.Machines)
        {
            var ent = EntityFor(pol, kv.Key);
            var prop = ent == null ? null : PropFor(ent, kv.Value.Column);
            if (ent == null || prop == null) continue;
            var m = kv.Value;
            string? State(string? s) => s == null ? null : m.States.FirstOrDefault(x => string.Equals(x, s, StringComparison.OrdinalIgnoreCase));
            foreach (var w in D.StatusWrites.Where(w => w.Model == ent.T.Name && Same(w.Column, prop.Col.Name) && w.Kind != "initial" && w.From is { Count: > 0 }))
            {
                var to = State(w.Value) ?? State(w.Stored);
                if (to == null) continue;
                var bad = w.From!.Select(State).Where(f => f != null && !string.Equals(f, to, StringComparison.OrdinalIgnoreCase) && !m.Edges.Any(e => string.Equals(e.From, f, StringComparison.OrdinalIgnoreCase) && string.Equals(e.To, to, StringComparison.OrdinalIgnoreCase))).Select(f => f!).Distinct().ToList();
                if (bad.Count == 0) continue;
                var subject = $"{kv.Key}.{kv.Value.Column}={w.Value}";
                found.Add(new CFinding
                {
                    Code = "DN808", File = w.File, Line = w.Line, Key = $"DN808|{w.Route}|{subject}",
                    Message = $"{w.Route} writes {kv.Key}.{kv.Value.Column} = {w.Value} at line {w.Line} from {string.Join(", ", bad)} (guard: `{w.Guard}`), and the state machine has no edge {bad[0]} -> {to}",
                    Hint = $"add the edge {bad[0]} -> {to} to the spec's machine, or refuse the write from {string.Join(", ", bad)}",
                });
            }
        }
    }
}
