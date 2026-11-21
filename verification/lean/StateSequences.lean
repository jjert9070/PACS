import ARules
import ABCRules

/- Transition specifications, not Python execution semantics.
   CAS assumes the storage boundary atomically checks and writes a version.
   Passive events exclude edits, explicit discard and owner changes. -/
namespace PacsRules.Sequences

structure Versioned where
  value : Nat
  version : Nat
  deriving DecidableEq

def commit (expected value : Nat) (stored : Versioned) : Bool × Versioned :=
  if expected = stored.version then (true, ⟨value, stored.version + 1⟩)
  else (false, stored)

theorem stale_commit_changes_nothing (expected value : Nat) (stored : Versioned)
    (stale : expected ≠ stored.version) : commit expected value stored = (false, stored) := by
  simp [commit, stale]

theorem successful_commit_advances (value : Nat) (stored : Versioned) :
    commit stored.version value stored = (true, ⟨value, stored.version + 1⟩) := by
  simp [commit]

theorem same_version_second_writer_rejected (first second : Nat) (stored : Versioned) :
    (commit stored.version second (commit stored.version first stored).2).1 = false := by
  simp [commit]

theorem same_version_second_writer_cannot_overwrite (first second : Nat) (stored : Versioned) :
    (commit stored.version second (commit stored.version first stored).2).2 =
      (commit stored.version first stored).2 := by
  simp [commit]

inductive PassiveEvent where
  | hide | reveal | queryFailure

def passiveStep (event : PassiveEvent) (panel : A.Panel) : A.Panel :=
  match event with
  | .hide => A.hide panel
  | .reveal => A.showPanel panel
  | .queryFailure => panel

def passiveRun : List PassiveEvent → A.Panel → A.Panel
  | [], panel => panel
  | event :: rest, panel => passiveRun rest (passiveStep event panel)

theorem passive_sequence_preserves_draft (events : List PassiveEvent) (panel : A.Panel) :
    (passiveRun events panel).draft = panel.draft := by
  induction events generalizing panel with
  | nil => rfl
  | cons event rest ih =>
    rw [passiveRun, ih]
    cases event <;> rfl

theorem repeated_dirty_reload_preserves_draft (screen : A.Screen) (first second : A.Snapshot)
    (dirty : screen.form ≠ screen.baseline) :
    (A.reload (A.reload screen first) second).form = screen.form := by
  simp [A.reload, dirty]

structure OwnedPanel where
  owner : String
  panel : Option A.Panel

def changeOwner (next : String) (state : OwnedPanel) : OwnedPanel :=
  if next = state.owner then state else ⟨next, none⟩

theorem changed_owner_has_no_previous_draft (next : String) (state : OwnedPanel)
    (different : next ≠ state.owner) : (changeOwner next state).panel = none := by
  simp [changeOwner, different]

def pipeline (selected : ABC.Identity) (stored : ABC.Original) (resultSource : String) :
    Option (ABC.Original × String) := do
  let opened ← ABC.openBound selected stored
  let processing ← ABC.attachProcessing opened resultSource
  pure (opened, processing)

theorem accepted_pipeline_keeps_identity_and_source (selected : ABC.Identity)
    (stored : ABC.Original) (resultSource : String) (output : ABC.Original × String)
    (accepted : pipeline selected stored resultSource = some output) :
    output.1.identity = selected ∧ output.2 = output.1.sourceId := by
  cases opening : ABC.openBound selected stored with
  | none => simp [pipeline, opening] at accepted
  | some opened =>
    cases processing : ABC.attachProcessing opened resultSource with
    | none => simp [pipeline, opening, processing] at accepted
    | some reference =>
      have same : (opened, reference) = output := by
        simpa [pipeline, opening, processing] using accepted
      cases same
      exact ⟨(ABC.opened_identity_and_frames selected stored opened opening).1,
        ABC.processing_belongs_to_opened_source opened resultSource reference processing⟩

theorem wrong_identity_rejected_before_processing (selected : ABC.Identity)
    (stored : ABC.Original) (resultSource : String) (wrong : stored.identity ≠ selected) :
    pipeline selected stored resultSource = none := by
  simp [pipeline, ABC.openBound, wrong]

#print axioms stale_commit_changes_nothing
#print axioms successful_commit_advances
#print axioms same_version_second_writer_rejected
#print axioms same_version_second_writer_cannot_overwrite
#print axioms passive_sequence_preserves_draft
#print axioms repeated_dirty_reload_preserves_draft
#print axioms changed_owner_has_no_previous_draft
#print axioms accepted_pipeline_keeps_identity_and_source
#print axioms wrong_identity_rejected_before_processing

end PacsRules.Sequences
