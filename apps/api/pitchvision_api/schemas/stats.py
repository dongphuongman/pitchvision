"""Bodies for /api/v1/results/{video_id}/stats and /heatmap (M3: SCR-20a Overview stats, SCR-20c Heatmaps).

Everything is measured over the OBSERVED time (main-camera frames with a usable pitch homography, people on screen);
``observed_player_s`` says how much that is. ``estimated_*`` fields extrapolate and must be labelled as estimates in
the UI (M3: "số ước tính có nhãn rõ")."""

from pydantic import BaseModel, Field


class ZonesM(BaseModel):
    """Distance per speed zone (FR-502): walk < 7.2, jog 7.2–14.4, run 14.4–25.2, sprint ≥ 25.2 km/h."""

    walk: float = 0.0
    jog: float = 0.0
    run: float = 0.0
    sprint: float = 0.0


class TopSpeed(BaseModel):
    kmh: float
    at_ms: int = Field(description="video time of the top speed (seek the player there)")
    track_id: int
    cluster: int | None = None


class ThirdsMs(BaseModel):
    """Possession by the ball's third of the pitch AS FILMED (attacking directions per half come with the match
    clock, M2)."""

    left: int = 0
    middle: int = 0
    right: int = 0


class TeamStats(BaseModel):
    cluster: int
    name: str | None
    color: str | None
    tracks: int = Field(description="tracks with observed positions (M1–M2: one per on-screen stretch, not per player)")
    observed_player_s: float = Field(description="Σ observed seconds over the team's tracks")
    distance_m: float = Field(description="observed distance (only what was seen)")
    zones_m: ZonesM
    sprints: int
    top_speed: TopSpeed | None
    m_per_min: float | None = Field(description="distance per observed player-minute (intensity)")
    estimated_distance_m: float | None = Field(
        description="ESTIMATE: m_per_min × 11 players × video minutes — label it as estimated")
    # M3-CV-02 (S9 ball part): null when the analysis has no possession data
    possession_pct: float | None = Field(None, description="share of the possessed time (FR-501)")
    possession_ms: int | None = None
    thirds_ms: ThirdsMs | None = None
    passes: int | None = Field(None, description="passes attempted (FR-505)")
    passes_completed: int | None = None
    balls_lost: int | None = Field(None, description="passes / touches that ended with the other team")


class PlayerStats(BaseModel):
    track_id: int
    cluster: int | None
    role: str | None
    observed_s: float
    distance_m: float
    top_speed_kmh: float | None
    top_speed_ms: int | None
    sprints: int
    avg_x: float | None = Field(description="average position, pitch metres (FR-504)")
    avg_y: float | None
    cov: list[float] | None = Field(description="[σxx², σyy², σxy] m² — the spread ellipse")


class PossessionMinute(BaseModel):
    minute: int = Field(description="video minute (0 = 0:00–0:59)")
    ms: dict[int, int] = Field(description="team cluster → ms of possession in that minute")


class StatsOut(BaseModel):
    available: bool = Field(description="false: the analysis predates stats (re-analyse / Map the pitch to get them)")
    revision: int
    duration_ms: int | None
    main_wide_ms: int = Field(description="time on the main (wide) camera — where positions can exist")
    teams: list[TeamStats]
    top_speeds: list[TopSpeed] = Field(description="fastest tracks, fastest first (players + goalkeepers)")
    players: list[PlayerStats] = Field(description="tracks observed ≥ min_observed_s, longest first")
    possession_timeline: list[PossessionMinute] = Field(default_factory=list,
                                                        description="possession over time (SCR-20a E03)")


class PassOut(BaseModel):
    t_ms: int = Field(description="the ball leaves the passer — seek a second before to watch it (SCR-20d E05)")
    end_ms: int
    cluster: int = Field(description="the passer's team")
    from_track: int
    to_track: int
    completed: bool
    x0: float
    y0: float
    x1: float
    y1: float


class PassList(BaseModel):
    items: list[PassOut]


class HeatmapOut(BaseModel):
    """Occupancy grid over the 105 × 68 m pitch from the 5 Hz positions (FR-503). ``counts`` is row-major
    (``rows`` × ``cols``, row 0 = y 0 m); each count is 0.2 s of one person in that cell. Smooth on the client."""

    cell_m: float
    cols: int
    rows: int
    length_m: float = 105.0
    width_m: float = 68.0
    sample_s: float = 0.2
    total: int
    counts: list[int]
