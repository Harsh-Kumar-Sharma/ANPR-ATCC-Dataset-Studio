# 04 --- System Architecture

## High-Level Flow

``` text
Video / RTSP
    |
    v
Source Adapter
    |
    v
Frame Decoder
    |
    v
Vehicle Detector
    |
    v
Tracker --------------------------+
    |                              |
    v                              |
Vehicle Track + Candidate Frames  |
    |                              |
    +----------+-----------+-------+
               |           |
               v           v
         Quality Score   OCR Candidates
               |           |
               +-----+-----+
                     v
              Smart Selector
                     |
                     v
              Human Review
                /         \
               v           v
        Approved Dataset   Hard/Failure Bank
               |
               v
         Version + Export
               |
               v
         Train / Evaluate
```

## Domain Boundaries

### Ingestion

Owns source metadata, decoding and timestamps.

### Perception

Detector produces observations. Tracker converts observations into
vehicle tracks.

### Selection

Ranks candidate frames. It does not erase track history.

### OCR

Evaluates suitable plate/frame candidates and stores all attempts.

### Annotation

Separates model suggestion from human truth.

### Dataset

Creates immutable/versioned views of approved annotations.

### Evaluation

Measures predictions against frozen truth and never mutates labels.

## Track Lifecycle

1.  Decode source frame.
2.  Detect vehicle observations.
3.  Associate observations to track.
4.  Store frame candidate and metrics.
5.  Close track after configured termination policy.
6.  Rank candidates.
7.  Run OCR on suitable candidates.
8.  Put track into review.
9.  Human approves/corrects.
10. Approved annotation becomes dataset-eligible.
