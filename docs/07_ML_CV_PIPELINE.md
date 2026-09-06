# 07 --- ML/CV Pipeline

## Detection

Use a YOLO-family detector through a stable adapter.

Normalized output: - bbox; - class ID/name; - confidence; - frame
timestamp.

Do not hard-wire application logic to one model filename/version.

## Tracking

Start with ByteTrack.

Track-level deduplication is the default approach. Tracker configuration
must tolerate short detection gaps so a blurred/fast vehicle is not
immediately split into a second vehicle.

Important evaluation metrics: - track fragmentation; - duplicate-track
rate; - missed-vehicle rate.

## Frame Quality Signals

Use multiple signals rather than one threshold: - sharpness/blur; -
vehicle pixel area; - crop completeness; - image-boundary truncation; -
detector confidence; - temporal stability; - plate visibility/OCR
confidence when available.

## Buckets

### BEST_DETECTION

Best representative frame for vehicle detection/classification.

### BEST_OCR

Best candidate for plate reading. It may be different from
`BEST_DETECTION`.

### HARD

Real vehicle with difficult conditions such as blur, low light,
occlusion, small size or poor angle.

### FAILED

Pipeline could not confidently process the vehicle. Preserve for review
and future model improvement.

## OCR

1.  Rank suitable frames inside the track.
2.  Identify/crop plate candidate.
3.  Run PaddleOCR.
4.  Normalize candidate text.
5.  If weak/failed, try alternate suitable frames.
6.  Store all attempts.
7.  Select track-level OCR using confidence/agreement.
8.  Allow human correction.

## Active Learning

Initial seed data is manually verified. Train a model, auto-label new
footage, and prioritize: - low-confidence predictions; - hard samples; -
failed samples; - class/model disagreement; - missed detections
discovered during QA.

The objective is not zero human review; it is maximum useful automation
with controlled ground truth quality.
