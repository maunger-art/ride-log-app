from db_store import (
    init_db,
    upsert_exercise,
    upsert_rep_scheme,
    upsert_norm_standard,
    count_norm_rows,
    list_rep_schemes,
)

def _ensure_rep_scheme(
    goal: str,
    phase: str,
    reps_min: int,
    reps_max: int,
    sets_min: int,
    sets_max: int,
    pct_1rm_min: float | None,
    pct_1rm_max: float | None,
    rpe_min: int | None,
    rpe_max: int | None,
    rest_min: int | None,
    rest_max: int | None,
    intent: str | None,
) -> None:
    """
    Idempotent insert for rep schemes (avoids duplicates on repeated seeding).
    We de-dupe by (goal, phase) as an MVP.
    """
    existing = list_rep_schemes(goal)
    for row in existing:
        # row = (id, goal, phase, reps_min, reps_max, sets_min, sets_max, pct_min, pct_max, rpe_min, rpe_max, rest_min, rest_max, intent)
        if str(row[2] or "") == str(phase or ""):
            return

    upsert_rep_scheme(
        goal, phase,
        reps_min, reps_max,
        sets_min, sets_max,
        pct_1rm_min, pct_1rm_max,
        rpe_min, rpe_max,
        rest_min, rest_max,
        intent
    )


def seed():
    init_db()

    # -----------------------------
    # Always seed exercises + rep schemes (safe/idempotent)
    # -----------------------------

    # --- Xmas / template exercises needed by Tab 4 block generator ---
    upsert_exercise("Bike Erg (High Seat)", "conditioning", "bilateral", "machine", "aerobic",
                    "Store reps as minutes (MVP).")
    upsert_exercise("Wall Sit", "squat", "bilateral", "bodyweight", "quads",
                    "Isometric hold; reps stored as seconds.")
    upsert_exercise("Isometric Single-Leg Hamstring Bridge", "hinge", "unilateral", "bodyweight", "hamstrings/glutes",
                    "Isometric; reps=seconds.")
    upsert_exercise("Isometric Split Squat", "squat", "unilateral", "kettlebell", "quads/glutes",
                    "Isometric; load optional.")
    upsert_exercise("Side Plank", "core", "unilateral", "bodyweight", "obliques",
                    "Reps=seconds.")
    upsert_exercise("Hip Abduction (Band, Seated)", "hip", "bilateral", "band", "glute med",
                    "Reps=count.")
    upsert_exercise("Single-Leg RDL", "hinge", "unilateral", "dumbbell", "hamstrings/glutes",
                    "Reps=count.")

    # -----------------------------
    # Exercises (initial library)
    # -----------------------------
    squat_id = upsert_exercise(
        "Back Squat", "squat", "bilateral", "barbell",
        "quads/glutes", "Use low-bar or high-bar as per athlete tolerance."
    )
    dl_id = upsert_exercise(
        "Deadlift", "hinge", "bilateral", "barbell",
        "posterior chain", "Trap bar can be substituted."
    )
    bench_id = upsert_exercise(
        "Bench Press", "push", "bilateral", "barbell",
        "pecs/triceps", None
    )
    ohp_id = upsert_exercise(
        "Overhead Press", "push", "bilateral", "barbell",
        "shoulders/triceps", None
    )
    pullup_id = upsert_exercise(
        "Pull-Up", "pull", "bilateral", "bodyweight",
        "lats/upper back", "Metric recorded as reps, not 1RM."
    )

    bss_id = upsert_exercise(
        "Bulgarian Split Squat", "squat", "unilateral", "dumbbell",
        "quads/glutes", "Rear-foot elevated split squat."
    )
    sl_rdl_id = upsert_exercise(
        "Single-Leg RDL", "hinge", "unilateral", "dumbbell",
        "hamstrings/glutes", None
    )
    stepup_id = upsert_exercise(
        "Step-Up", "squat", "unilateral", "dumbbell",
        "quads/glutes", "Use step height near knee level for standardisation."
    )

    calf_raise_id = upsert_exercise(
        "Single-Leg Calf Raise", "ankle", "unilateral", "bodyweight",
        "gastroc/soleus", "Metric not standardised in v1; use reps/RPE."
    )
    hip_thrust_id = upsert_exercise(
        "Hip Thrust", "hinge", "bilateral", "barbell",
        "glutes", "Alternative to deadlift for reduced spinal load."
    )

    # -----------------------------
    # Rep schemes by goal (idempotent)
    # -----------------------------
    _ensure_rep_scheme("endurance", "base", 12, 20, 2, 4, 0.55, 0.70, 5, 7, 45, 90,
                       "Controlled; continuous tension")
    _ensure_rep_scheme("hypertrophy", "base", 8, 12, 3, 5, 0.65, 0.80, 6, 8, 60, 120,
                       "Controlled eccentric; crisp concentric")
    _ensure_rep_scheme("strength", "build", 3, 6, 3, 6, 0.80, 0.92, 7, 9, 120, 240,
                       "Max intent; full rest")
    _ensure_rep_scheme("power", "peak", 2, 5, 3, 6, 0.30, 0.60, 5, 7, 90, 180,
                       "Explosive concentric; stop before speed drops")

    # -----------------------------
    # Normative standards
    # Only seed norms if empty (avoid duplicating)
    # -----------------------------
    if count_norm_rows() > 0:
        print("Seed: exercises/rep_schemes ensured. Norms already exist, skipping norm inserts.")
        return

    SRC = "Internal endurance-athlete benchmarks (v1) – Technique/Benchmark PS"

    def add_age_bands(ex_id, sex, metric, p, f, g, e, source, notes=None):
        upsert_norm_standard(ex_id, sex, 18, 39, metric, p, f, g, e, source, notes)

        if metric == "rel_1rm_bw":
            upsert_norm_standard(ex_id, sex, 40, 54, metric, p*0.90, f*0.90, g*0.90, e*0.90, source, "Adjusted ~10% for age.")
            upsert_norm_standard(ex_id, sex, 55, 65, metric, p*0.80, f*0.80, g*0.80, e*0.80, source, "Adjusted ~20% for age.")
        else:
            upsert_norm_standard(ex_id, sex, 40, 54, metric, max(0, p-1), max(0, f-1), max(0, g-1), max(0, e-2), source, "Adjusted reps for age.")
            upsert_norm_standard(ex_id, sex, 55, 65, metric, max(0, p-2), max(0, f-2), max(0, g-2), max(0, e-3), source, "Adjusted reps for age.")

    # Male standards
    add_age_bands(squat_id, "male", "rel_1rm_bw", 0.80, 1.00, 1.20, 1.50, SRC)
    add_age_bands(dl_id,    "male", "rel_1rm_bw", 1.00, 1.20, 1.50, 1.80, SRC)
    add_age_bands(bench_id, "male", "rel_1rm_bw", 0.60, 0.80, 1.00, 1.25, SRC)
    add_age_bands(ohp_id,   "male", "rel_1rm_bw", 0.30, 0.50, 0.70, 0.90, SRC)
    add_age_bands(hip_thrust_id, "male", "rel_1rm_bw", 0.90, 1.10, 1.40, 1.70, SRC,
                  "Useful substitute if deadlift tolerance limited.")
    add_age_bands(pullup_id, "male", "pullup_reps", 2, 5, 10, 15, SRC)

    add_age_bands(bss_id,    "male", "rel_1rm_bw", 0.60, 0.80, 1.00, 1.20, SRC)
    add_age_bands(sl_rdl_id, "male", "rel_1rm_bw", 0.40, 0.60, 0.80, 1.00, SRC)
    add_age_bands(stepup_id, "male", "rel_1rm_bw", 0.40, 0.60, 0.80, 1.00, SRC)

    # Female standards
    add_age_bands(squat_id, "female", "rel_1rm_bw", 0.50, 0.70, 0.90, 1.20, SRC)
    add_age_bands(dl_id,    "female", "rel_1rm_bw", 0.70, 0.90, 1.10, 1.40, SRC)
    add_age_bands(bench_id, "female", "rel_1rm_bw", 0.40, 0.50, 0.70, 0.90, SRC)
    add_age_bands(ohp_id,   "female", "rel_1rm_bw", 0.20, 0.30, 0.50, 0.70, SRC)
    add_age_bands(hip_thrust_id, "female", "rel_1rm_bw", 0.70, 0.90, 1.10, 1.40, SRC,
                  "Useful substitute if deadlift tolerance limited.")
    add_age_bands(pullup_id, "female", "pullup_reps", 0, 3, 6, 10, SRC)

    add_age_bands(bss_id,    "female", "rel_1rm_bw", 0.40, 0.50, 0.65, 0.80, SRC)
    add_age_bands(sl_rdl_id, "female", "rel_1rm_bw", 0.30, 0.50, 0.60, 0.80, SRC)
    add_age_bands(stepup_id, "female", "rel_1rm_bw", 0.30, 0.40, 0.50, 0.70, SRC)

    print("Seed complete: exercises, rep schemes, and norm standards inserted.")


if __name__ == "__main__":
    seed()
