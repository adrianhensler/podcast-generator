from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class ProjectCreate(BaseModel):
    url: str
    num_speakers: int = 2
    tone: str = "neutral"
    length: str = "medium"
    use_tavily: bool = False
    host_a_voice: str = "Wise_Woman"
    host_b_voice: str = "Deep_Voice_Man"


class StageLogOut(BaseModel):
    id: int
    stage: str
    model: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: Optional[int]
    error: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ArtifactOut(BaseModel):
    id: int
    artifact_type: str
    file_path: str
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectOut(BaseModel):
    id: str
    url: str
    title: Optional[str]
    status: str
    error_message: Optional[str]
    host_a_voice: str
    host_b_voice: str
    num_speakers: int
    tone: str
    length: str
    use_tavily: bool
    total_tokens: int
    estimated_cost_usd: float
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactOut] = []
    stage_logs: list[StageLogOut] = []

    class Config:
        from_attributes = True


class ArtifactContent(BaseModel):
    content: str


class VoiceUpdate(BaseModel):
    host_a_voice: str
    host_b_voice: str
