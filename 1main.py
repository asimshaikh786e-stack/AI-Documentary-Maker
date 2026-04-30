from fastapi import FastAPI
from pydantic import BaseModel
import uuid
from orchestrator import start_pipeline, Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Documentary Maker")

class DocRequest(BaseModel):
    prompt: str
    title: str

@app.post("/api/create")
def create_documentary(req: DocRequest):
    project_id = int(uuid.uuid4().int % 10000)
    task_id = start_pipeline(project_id, req.prompt)
    
    return {
        "message": "Documentary pipeline started!",
        "project_id": project_id,
        "task_id": task_id,
        "title": req.title
    }

@app.get("/health")
def health_check():
    return {"status": "Running"}
