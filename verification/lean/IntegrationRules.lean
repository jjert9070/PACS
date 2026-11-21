import Std

/- Closed six-status model; no filesystem, network or concurrency semantics. -/
namespace PacsRules

namespace BC
inductive Status where
  | registered | reused | rejected | conflict | failed | savedNotLinked
  deriving DecidableEq, Repr

def completed : Status → Bool
  | .registered | .reused => true
  | _ => false

theorem partial_is_not_completed : completed .savedNotLinked = false := by
  rfl

theorem failures_are_not_completed :
    completed .rejected = false ∧ completed .conflict = false ∧ completed .failed = false := by
  decide

def countCompleted : List Status → Nat
  | [] => 0
  | s :: rest => (if completed s then 1 else 0) + countCompleted rest

theorem partial_does_not_increase_count (rest : List Status) :
    countCompleted (.savedNotLinked :: rest) = countCompleted rest := by
  simp [countCompleted, completed]

theorem count_append (xs ys : List Status) :
    countCompleted (xs ++ ys) = countCompleted xs + countCompleted ys := by
  induction xs with
  | nil => simp [countCompleted]
  | cons x xs ih => simp [countCompleted, ih, Nat.add_assoc]

theorem count_bounded_by_files (xs : List Status) : countCompleted xs ≤ xs.length := by
  induction xs with
  | nil => simp [countCompleted]
  | cons x xs ih => cases x <;> simp_all [countCompleted, completed] <;> omega
end BC

#print axioms BC.partial_is_not_completed
#print axioms BC.failures_are_not_completed
#print axioms BC.partial_does_not_increase_count
#print axioms BC.count_append
#print axioms BC.count_bounded_by_files
end PacsRules
