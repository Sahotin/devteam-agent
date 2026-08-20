from pydantic import BaseModel


class DeliveryCommand(BaseModel):
    label: str
    command: str
    description: str


class ProjectFileInfo(BaseModel):
    path: str
    kind: str
    description: str


class DeliveryGuide(BaseModel):
    project_name: str
    root_path: str
    project_type: str
    summary: str
    entry_point: str | None = None
    start_commands: list[DeliveryCommand]
    structure: list[ProjectFileInfo]
    operation_steps: list[str]
    notes: list[str]
