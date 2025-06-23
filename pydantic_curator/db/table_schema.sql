CREATE TABLE Criterion (
    TrialId TEXT,
    RuleNum INTEGER,
    RuleId TEXT,
    Description TEXT,
    Confidence REAL,
    Checked INTEGER,
    Formatted TEXT
);

CREATE TABLE CriterionOverride (
    TrialId TEXT,
    RuleNum INTEGER,
    Description TEXT,
    Formatted TEXT
);
