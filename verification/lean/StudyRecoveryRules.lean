import Std

/- Error recovery after one failed write. A found record has already passed the
   Python decoder's identity checks. Versions are opaque strings, not counters.
   HTTP transport, server atomicity and decoding are not proved by this model. -/
namespace PacsRules.Recovery

inductive ReadResult where
  | found (version : String) (samePayload : Bool)
  | failed (status : Option Nat)
  deriving DecidableEq

inductive Outcome where
  | accepted
  | conflict
  | requestError (status : Option Nat)
  deriving DecidableEq

def recoverable (status : Option Nat) : Bool :=
  [some 400, some 403, some 409, some 412].contains status

theorem recovery_statuses_are_exact (status : Option Nat) :
    recoverable status = true ↔
      status = some 400 ∨ status = some 403 ∨ status = some 409 ∨ status = some 412 := by
  simp [recoverable]

def recover (status : Option Nat) (expected : Option String) (read : ReadResult) : Outcome :=
  if recoverable status then
    match read with
    | .failed readStatus =>
      if readStatus = some 404 then .requestError status else .requestError readStatus
    | .found version samePayload =>
      match expected with
      | none => if samePayload then .accepted else .conflict
      | some prior => if version = prior then .requestError status else .conflict
  else .requestError status

theorem unrelated_error_is_preserved (status : Option Nat) (expected : Option String)
    (read : ReadResult) (unrelated : recoverable status = false) :
    recover status expected read = .requestError status := by
  simp [recover, unrelated]

theorem unrelated_error_ignores_read (status : Option Nat) (expected : Option String)
    (first second : ReadResult) (unrelated : recoverable status = false) :
    recover status expected first = recover status expected second := by
  simp [recover, unrelated]

theorem missing_record_preserves_write_error (status : Option Nat) (expected : Option String) :
    recover status expected (.failed (some 404)) = .requestError status := by
  simp [recover]

theorem failed_read_preserves_read_error (status readStatus : Option Nat)
    (expected : Option String) (inspect : recoverable status = true)
    (notMissing : readStatus ≠ some 404) :
    recover status expected (.failed readStatus) = .requestError readStatus := by
  simp [recover, inspect, notMissing]

theorem identical_create_retry_is_accepted (status : Option Nat) (version : String)
    (inspect : recoverable status = true) :
    recover status none (.found version true) = .accepted := by
  simp [recover, inspect]

theorem different_create_retry_is_conflict (status : Option Nat) (version : String)
    (inspect : recoverable status = true) :
    recover status none (.found version false) = .conflict := by
  simp [recover, inspect]

theorem stale_update_is_conflict (status : Option Nat) (prior current : String)
    (samePayload : Bool) (inspect : recoverable status = true) (stale : current ≠ prior) :
    recover status (some prior) (.found current samePayload) = .conflict := by
  simp [recover, inspect, stale]

theorem same_version_preserves_write_error (status : Option Nat) (version : String)
    (samePayload : Bool) :
    recover status (some version) (.found version samePayload) = .requestError status := by
  simp [recover]

theorem failed_read_is_never_success (status readStatus : Option Nat)
    (expected : Option String) :
    recover status expected (.failed readStatus) ≠ .accepted := by
  simp only [recover]
  split <;> simp_all
  split <;> simp_all

theorem failed_read_is_never_conflict (status readStatus : Option Nat)
    (expected : Option String) :
    recover status expected (.failed readStatus) ≠ .conflict := by
  simp only [recover]
  split <;> simp_all
  split <;> simp_all

theorem update_error_is_never_success (status : Option Nat) (prior current : String)
    (samePayload : Bool) :
    recover status (some prior) (.found current samePayload) ≠ .accepted := by
  simp only [recover]
  split <;> simp_all
  split <;> simp_all

#print axioms recovery_statuses_are_exact
#print axioms unrelated_error_is_preserved
#print axioms unrelated_error_ignores_read
#print axioms missing_record_preserves_write_error
#print axioms failed_read_preserves_read_error
#print axioms identical_create_retry_is_accepted
#print axioms different_create_retry_is_conflict
#print axioms stale_update_is_conflict
#print axioms same_version_preserves_write_error
#print axioms failed_read_is_never_success
#print axioms failed_read_is_never_conflict
#print axioms update_error_is_never_success

end PacsRules.Recovery
