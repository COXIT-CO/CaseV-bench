from datetime import datetime, UTC
from app.enums import PromptStatus
from app.extensions import db


def utc_now():
    return datetime.now(UTC)


class Prompt(db.Model):
    __tablename__ = "prompts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    system_prompt = db.Column(db.Text, nullable=False, default="")
    expected_json = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    runs = db.relationship("PromptRun", back_populates="prompt", cascade="all, delete-orphan")


class PromptRun(db.Model):
    __tablename__ = "prompt_runs"

    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey("prompts.id"), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    workflow = db.Column(db.String(32), nullable=False, default="count")
    dpi = db.Column(db.Integer, nullable=False, default=200)
    tile_size = db.Column(db.Integer, nullable=False, default=1400)
    tile_overlap_pct = db.Column(db.Integer, nullable=False, default=20)
    status = db.Column(db.Enum(PromptStatus), nullable=False, default=PromptStatus.PENDING)
    response = db.Column(db.Text, nullable=True)
    result_json = db.Column(db.Text, nullable=True)
    artifacts_path = db.Column(db.String(512), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    prompt = db.relationship("Prompt", back_populates="runs")
