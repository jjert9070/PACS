import Std

/- A state specification, not a proof of Python or Streamlit execution.
   Fields abstract the three detail widgets. Versions are equality tokens.
   Hidden-widget retention is checked separately with Streamlit AppTest. -/
namespace PacsRules.A

structure Fields where
  acquiredOn : String
  linkage : String
  receipt : String
  deriving DecidableEq

structure Snapshot where
  fields : Fields
  version : Nat

structure Screen where
  form : Fields
  baseline : Fields
  saved : Snapshot

def reload (screen : Screen) (latest : Snapshot) : Screen :=
  if screen.form = screen.baseline then
    ⟨latest.fields, latest.fields, latest⟩
  else
    { screen with saved := latest }

theorem clean_reload_uses_latest (screen : Screen) (latest : Snapshot)
    (clean : screen.form = screen.baseline) :
    (reload screen latest).form = latest.fields ∧
      (reload screen latest).baseline = latest.fields ∧
      (reload screen latest).saved.version = latest.version := by
  simp [reload, clean]

theorem dirty_reload_keeps_draft (screen : Screen) (latest : Snapshot)
    (dirty : screen.form ≠ screen.baseline) :
    (reload screen latest).form = screen.form ∧
      (reload screen latest).baseline = screen.baseline := by
  simp [reload, dirty]

structure Panel where
  draft : Fields
  visible : Bool

def hide (panel : Panel) : Panel := { panel with visible := false }
def showPanel (panel : Panel) : Panel := { panel with visible := true }

theorem hide_show_preserves_draft (panel : Panel) :
    (showPanel (hide panel)).draft = panel.draft := by
  rfl

def acceptsVersion (latest expected : Nat) : Bool := decide (latest = expected)

theorem stale_version_rejected (latest expected : Nat) (stale : latest ≠ expected) :
    acceptsVersion latest expected = false := by
  simp [acceptsVersion, stale]

def switchSelection (current target : Nat) (caseDirty detailsDirty discard : Bool) : Nat :=
  if (!caseDirty && !detailsDirty) || discard then target else current

theorem dirty_details_block_switch (current target : Nat) (caseDirty : Bool) :
    switchSelection current target caseDirty true false = current := by
  cases caseDirty <;> rfl

theorem explicit_discard_allows_switch (current target : Nat) (caseDirty detailsDirty : Bool) :
    switchSelection current target caseDirty detailsDirty true = target := by
  cases caseDirty <;> cases detailsDirty <;> rfl

#print axioms clean_reload_uses_latest
#print axioms dirty_reload_keeps_draft
#print axioms hide_show_preserves_draft
#print axioms stale_version_rejected
#print axioms dirty_details_block_switch
#print axioms explicit_discard_allows_switch

end PacsRules.A
