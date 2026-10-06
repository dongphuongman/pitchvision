"""Team split (S3 calibrate, S4 per-track vote): shirt crops → SigLIP embeddings → PCA → KMeans(2) + outliers."""
from cv_core.teams.colors import cluster_colours
from cv_core.teams.embed import (
    SIGLIP_MODEL,
    Embedder,
    SiglipEmbedder,
    preprocess,
    torso_box,
    torso_crop,
)
from cv_core.teams.model import (
    NO_TEAM,
    TEAM_MODEL_FORMAT,
    FitParams,
    TeamModel,
    TeamSeparationFailed,
    fit_seeded_team_model,
    fit_team_model,
    kmeans,
    pca,
    representative,
    silhouette,
    vote,
)
from cv_core.teams.voting import TrackTeamVoter, team_column

__all__ = [
    "NO_TEAM",
    "SIGLIP_MODEL",
    "TEAM_MODEL_FORMAT",
    "Embedder",
    "FitParams",
    "SiglipEmbedder",
    "TeamModel",
    "TeamSeparationFailed",
    "TrackTeamVoter",
    "cluster_colours",
    "fit_seeded_team_model",
    "fit_team_model",
    "kmeans",
    "pca",
    "preprocess",
    "representative",
    "silhouette",
    "team_column",
    "torso_box",
    "torso_crop",
    "vote",
]
