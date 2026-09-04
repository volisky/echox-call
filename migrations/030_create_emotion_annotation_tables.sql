CREATE TABLE IF NOT EXISTS emotion_annotation_audio_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type text NOT NULL,
    source_reference text NOT NULL,
    stored_path text NOT NULL,
    original_filename text NOT NULL,
    content_type text,
    size_bytes bigint,
    duration_sec numeric(10, 3),
    imported_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT emotion_annotation_audio_source_type_valid CHECK (
        source_type IN ('upload', 'server')
    ),
    CONSTRAINT emotion_annotation_audio_text_not_blank CHECK (
        btrim(source_reference) <> ''
        AND btrim(stored_path) <> ''
        AND btrim(original_filename) <> ''
        AND btrim(imported_by) <> ''
    ),
    CONSTRAINT emotion_annotation_audio_size_valid CHECK (
        size_bytes IS NULL OR size_bytes >= 0
    ),
    CONSTRAINT emotion_annotation_audio_duration_valid CHECK (
        duration_sec IS NULL OR duration_sec >= 0
    ),
    CONSTRAINT emotion_annotation_audio_stored_path_key UNIQUE (stored_path)
);

CREATE INDEX IF NOT EXISTS idx_emotion_annotation_audio_created_at
    ON emotion_annotation_audio_files (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_emotion_annotation_audio_source_reference
    ON emotion_annotation_audio_files (source_type, source_reference);

CREATE TABLE IF NOT EXISTS emotion_annotation_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    audio_file_id uuid NOT NULL REFERENCES emotion_annotation_audio_files(id) ON DELETE CASCADE,
    annotator_username text NOT NULL,
    annotator_name text NOT NULL,
    revision_no integer NOT NULL,
    audio_usable boolean NOT NULL DEFAULT true,
    overall_note text NOT NULL DEFAULT '',
    guideline_version text NOT NULL DEFAULT 'v1',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT emotion_annotation_session_annotator_not_blank CHECK (
        btrim(annotator_username) <> ''
        AND btrim(annotator_name) <> ''
    ),
    CONSTRAINT emotion_annotation_session_revision_valid CHECK (revision_no > 0),
    CONSTRAINT emotion_annotation_session_unique_revision UNIQUE (
        audio_file_id, annotator_username, revision_no
    )
);

CREATE INDEX IF NOT EXISTS idx_emotion_annotation_sessions_audio_created_at
    ON emotion_annotation_sessions (audio_file_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_emotion_annotation_sessions_annotator_created_at
    ON emotion_annotation_sessions (annotator_username, created_at DESC);

CREATE TABLE IF NOT EXISTS emotion_annotation_segments (
    id bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES emotion_annotation_sessions(id) ON DELETE CASCADE,
    start_sec numeric(10, 3) NOT NULL,
    end_sec numeric(10, 3) NOT NULL,
    speaker_label text,
    primary_emotion text NOT NULL,
    secondary_emotions text[] NOT NULL DEFAULT ARRAY[]::text[],
    is_overlapping_speech boolean NOT NULL DEFAULT false,
    annotator_confidence smallint NOT NULL DEFAULT 2,
    needs_review boolean NOT NULL DEFAULT false,
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT emotion_annotation_segment_time_valid CHECK (
        start_sec >= 0
        AND end_sec > start_sec
    ),
    CONSTRAINT emotion_annotation_segment_primary_valid CHECK (
        primary_emotion IN (
            'Anger', 'Contempt', 'Disgust', 'Fear', 'Happiness',
            'Neutral', 'Sadness', 'Surprise', 'Other'
        )
    ),
    CONSTRAINT emotion_annotation_segment_secondary_valid CHECK (
        cardinality(secondary_emotions) <= 2
        AND secondary_emotions <@ ARRAY[
            'Anger', 'Contempt', 'Disgust', 'Fear', 'Happiness',
            'Neutral', 'Sadness', 'Surprise', 'Other'
        ]::text[]
        AND NOT (primary_emotion = ANY(secondary_emotions))
        AND NOT ('Neutral' = ANY(secondary_emotions))
        AND NOT ('Other' = ANY(secondary_emotions))
    ),
    CONSTRAINT emotion_annotation_segment_confidence_valid CHECK (
        annotator_confidence IN (1, 2, 3)
    )
);

CREATE INDEX IF NOT EXISTS idx_emotion_annotation_segments_session_time
    ON emotion_annotation_segments (session_id, start_sec, end_sec);
