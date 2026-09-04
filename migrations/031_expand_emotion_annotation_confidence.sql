ALTER TABLE emotion_annotation_segments
    DROP CONSTRAINT IF EXISTS emotion_annotation_segment_confidence_valid;

UPDATE emotion_annotation_segments
SET annotator_confidence = CASE annotator_confidence
    WHEN 1 THEN 1
    WHEN 2 THEN 3
    WHEN 3 THEN 5
    ELSE annotator_confidence
END;

ALTER TABLE emotion_annotation_segments
    ALTER COLUMN annotator_confidence SET DEFAULT 3;

ALTER TABLE emotion_annotation_segments
    ADD CONSTRAINT emotion_annotation_segment_confidence_valid CHECK (
        annotator_confidence BETWEEN 1 AND 5
    );

COMMENT ON COLUMN emotion_annotation_segments.annotator_confidence IS
    '人工标注把握程度，1 表示很低，5 表示很高；训练软标签聚合时用作投票权重';
