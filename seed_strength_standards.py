from db_store import (
    init_db,
    upsert_exercise,
    upsert_rep_scheme,
    upsert_norm_standard,
    count_norm_rows
)

def seed():
    init_db()

    # Avoid duplicating seed if already populated
    if count_norm_rows() > 0:
        print("Seed skipped: norm_strength_standards already contains rows.")
        return

    # -----------------------------
    # Exercises (initial library)
    # -----------------------------
    # Bilateral compound
    squat_id = upsert_exercise("Back Squat", "squat", "bilateral", "barbell", "quads/glutes", "Use low-bar or high-bar as per athlete tolerance.")
    dl_id = upsert_exercise("Deadlift", "hinge", "bilateral", "barbell", "posterior chain", "Trap bar can be substituted.")
    bench_id = upsert_exercise("Bench Press", "push", "bilateral", "barbell", "pecs/triceps", None)
    ohp_id = upsert_exercise("Overhead Press", "push", "bilateral", "barbell", "shoulders/triceps", None)
    pullup_id = upsert_exercise("Pull-Up", "pull", "bilateral", "bodyweight", "lats/upper back", "Metric recorded as reps, not 1RM.")

    # Unilateral
    bss_id = upsert_exercise("Bulgarian Split Squat", "squat", "unilateral", "dumbbell", "quads/glutes", "Rear-foot elevated split squat.")
    sl_rdl_id = upsert_exercise("Single-Leg RDL", "hinge", "unilateral", "dumbbell", "hamstrings/glutes", None)
    stepup_id = upsert_exercise("Step-Up", "squat", "unilateral", "dumbbell", "quads/glutes", "Use step height near knee level for strength standardisation.")

    # -----------------------------
    # Rep schemes by goal (MVP)
    # %1RM ranges are typical guidance.
    # -----------------------------
    # Endurance / capacity (higher reps, moderate load, controlled tempo)
    upsert_rep_scheme("endurance", "base", 12, 20, 2, 4, 0.55, 0.70, 5, 7, 45, 90, "Controlled; continuous tension")
    # Hypertrophy (moderate reps, moderate load)
    upsert_rep_scheme("hypertrophy", "base", 8, 12, 3, 5, 0.65, 0.80, 6, 8, 60, 120, "Controlled eccentric; crisp concentric")
    # Strength (low reps, high load)
    upsert_rep_scheme("strength", "build", 3, 6, 3, 6, 0.80, 0.92, 7, 9, 120, 240, "Max intent; full rest")
    # Power (low reps, lighter load, maximal speed)
    upsert_rep_scheme("power", "peak", 2, 5, 3, 6, 0.30, 0.60, 5, 7, 90, 180, "Explosive concentric; stop before speed drops")

    # -----------------------------
    # Normative standards (relative to BW)
    # metric="rel_1rm_bw" for lifts; "pullup_reps" for pull-ups
    #
    # These are pragmatic endurance-athlete oriented thresholds (not powerlifting elite norms).
    # Apply age bands 18–39, 40–54, 55–65 with modest downshifts.
    # -----------------------------

    # Helper to add age bands with simple decrements
    def add_age_bands(ex_id, sex, metric, p, f, g, e, source):
        # 18–39: baseline
        upsert_norm_standard(ex_id, sex, 18, 39, metric, p, f, g, e, source, None)
        # 40–54: ~10% decrement for load-based metrics
        if metric == "rel_1rm_bw":
            upsert_norm_standard(ex_id, sex, 40, 54, metric, p*0.90, f*0.90, g*0.90, e*0.90, source, "Adjusted ~10% for age.")
            upsert_norm_standard(ex_id, sex, 55, 65, metric, p*0.80, f*0.80, g*0.80, e*0.80, source, "Adjusted ~20% for age.")
        else:
            # pull-up reps – decrement smaller
            upsert_norm_standard(ex_id, sex, 40, 54, metric, max(0, p-1), max(0, f-1), max(0, g-1), max(0, e-2), source, "Adjusted reps for age.")
            upsert_norm_standard(ex_id, sex, 55, 65, metric, max(0, p-2), max(0, f-2), max(0, g-2), max(0, e-3), source, "Adjusted reps for age.")

    SRC = "Internal endurance-athlete benchmarks (v1) – Technique/Benchmark PS"

    # Male compound
    add_age_bands(squat_id, "male", "rel_1rm_bw", 0.8, 1.0, 1.2, 1.5, SRC)
    add_age_bands(dl_id, "male", "rel_1rm_bw", 1.0, 1.2, 1.5, 1.8, SRC)
    add_age_bands(bench_id, "male", "rel_1rm_bw", 0.6, 0.8, 
