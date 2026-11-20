import Std

/- ABC boundary specification. Strings stand for validated identifiers and hashes.
   DICOM decoding, byte hashing, filesystem and Firebase behavior are not modeled.
   Real B/C functions and AppTest independently check these Python boundaries. -/
namespace PacsRules.ABC

structure Identity where
  issuer : String
  patient : String
  study : String
  series : String
  object : String
  deriving DecidableEq

structure Original where
  identity : Identity
  sourceId : String
  objectFrames : Nat
  displayFrames : Nat
  deriving DecidableEq

def openBound (selected : Identity) (stored : Original) : Option Original :=
  if stored.identity = selected ∧ stored.objectFrames = 1 ∧ stored.displayFrames = 1 then
    some stored
  else none

theorem opened_identity_and_frames (selected : Identity) (stored opened : Original)
    (accepted : openBound selected stored = some opened) :
    opened.identity = selected ∧ opened.objectFrames = 1 ∧ opened.displayFrames = 1 := by
  unfold openBound at accepted
  split at accepted
  next valid => cases accepted; exact valid
  next => contradiction

theorem unsupported_frames_rejected (selected : Identity) (stored : Original)
    (unsupported : stored.objectFrames ≠ 1) : openBound selected stored = none := by
  simp [openBound, unsupported]

def attachProcessing (opened : Original) (resultSource : String) : Option String :=
  if resultSource = opened.sourceId then some resultSource else none

theorem processing_belongs_to_opened_source (opened : Original) (resultSource accepted : String)
    (bound : attachProcessing opened resultSource = some accepted) : accepted = opened.sourceId := by
  unfold attachProcessing at bound
  split at bound
  next same => cases bound; exact same
  next => contradiction

structure Bounds where
  left : Int
  top : Int
  right : Int
  bottom : Int
  deriving DecidableEq

def validBounds (width height : Nat) (roi : Bounds) : Prop :=
  0 ≤ roi.left ∧ roi.left < roi.right ∧ roi.right ≤ width ∧
    0 ≤ roi.top ∧ roi.top < roi.bottom ∧ roi.bottom ≤ height

instance (width height : Nat) (roi : Bounds) : Decidable (validBounds width height roi) :=
  inferInstanceAs (Decidable (0 ≤ roi.left ∧ roi.left < roi.right ∧ roi.right ≤ (width : Int) ∧
    0 ≤ roi.top ∧ roi.top < roi.bottom ∧ roi.bottom ≤ (height : Int)))

def acceptRoi (width height : Nat) (roi : Bounds) : Option Bounds :=
  if validBounds width height roi then some roi else none

theorem accepted_roi_is_in_bounds (width height : Nat) (roi accepted : Bounds)
    (bound : acceptRoi width height roi = some accepted) : validBounds width height accepted := by
  unfold acceptRoi at bound
  split at bound
  next valid => cases bound; exact valid
  next => contradiction

theorem invalid_roi_rejected (width height : Nat) (roi : Bounds)
    (invalid : ¬ validBounds width height roi) : acceptRoi width height roi = none := by
  simp [acceptRoi, invalid]

#print axioms opened_identity_and_frames
#print axioms unsupported_frames_rejected
#print axioms processing_belongs_to_opened_source
#print axioms accepted_roi_is_in_bounds
#print axioms invalid_roi_rejected

end PacsRules.ABC
