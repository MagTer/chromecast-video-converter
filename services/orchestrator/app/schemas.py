from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import config as config_module


class ScanRequest(BaseModel):
    library: Optional[str] = None
    root: Optional[str] = None


class EventPayload(BaseModel):
    path: str
    library: Optional[str] = None
    event: str = Field(default="created")
    size: Optional[int] = None
    modified_at: Optional[datetime] = None
    is_directory: bool = False


class EventBatch(BaseModel):
    events: List[EventPayload]


class JobStatusPayload(BaseModel):
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None
    return_code: Optional[int] = None
    logs: Optional[list] = None
    pipeline: Optional[dict] = None
    compliance: Optional[dict] = None


class JobAckPayload(BaseModel):
    delivery_id: str


class QueuePauseRequest(BaseModel):
    reason: Optional[str] = None


class ReprocessPayload(BaseModel):
    profile_id: Optional[int] = None


class LibraryProfilePayload(BaseModel):
    profile_id: int


class EntryProfilePayload(BaseModel):
    profile_id: int


class LibraryCreatePayload(BaseModel):
    name: str = Field(min_length=1)
    root: str = Field(min_length=1, description="Absolute path to the library root")
    depth: Optional[str] = Field(default=None)
    profile_id: int


class WorkerTelemetryPayload(BaseModel):
    worker_id: str
    gpu_available: bool
    devices: List[str] = Field(default_factory=list)
    cuda_available: bool
    nvenc_available: bool
    checked_at: datetime
    message: Optional[str] = None


class LibraryEntryResponse(BaseModel):
    id: int
    path: str
    library: str
    profile: str
    profile_id: Optional[int] = None
    decode_type: Optional[str] = None
    scale_type: Optional[str] = None
    encode_type: Optional[str] = None
    status: str
    output_path: Optional[str] = None
    last_error: Optional[str] = None
    last_job_id: Optional[str] = None
    original_missing: bool
    output_compliant: Optional[bool] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True, json_encoders={datetime: lambda value: value.isoformat()}
    )


class HardwareEncodingPayload(BaseModel):
    codec: str
    profile: str
    level: str
    resolution: str
    max_fps: int = Field(default=30, gt=0, le=60)
    bitrate: str = Field(default="8M")
    max_bitrate: str
    bufsize: str
    preset: str
    cq: int = Field(ge=0, le=40)
    rc: str
    bframes: int = Field(default=2, ge=0, le=3)
    lookahead: int = Field(default=24, ge=0, le=32)
    adaptive_b_frames: bool = Field(default=True)
    aq: bool = Field(default=True)
    spatial_aq: bool = Field(default=True)
    temporal_aq: bool = Field(default=True)
    aq_strength: int = Field(default=7, ge=5, le=10)
    audio: config_module.AudioProfile


class EncodingUpdatePayload(BaseModel):
    name: str = Field(description="Profile name to upsert")
    gpu: HardwareEncodingPayload
    cpu: HardwareEncodingPayload


class LoggingUpdatePayload(BaseModel):
    retention_days: int = Field(ge=1, le=90)


class OperationalUpdatePayload(BaseModel):
    scan_interval_min: int = Field(
        ge=0, description="Scheduled scan interval in minutes (0=disabled)"
    )


class LogIngestEvent(BaseModel):
    logger: str
    level: str
    message: str
    severity: Optional[str] = None
    source: Optional[str] = None
    category: Optional[str] = None
    timestamp: Optional[datetime] = None


class LogIngestBatch(BaseModel):
    entries: List[LogIngestEvent]
