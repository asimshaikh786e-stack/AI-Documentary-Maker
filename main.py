from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import uuid
from orchestrator import start_pipeline, Base, engine

# Create DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Documentary Maker API")

class DocRequest(BaseModel):
    prompt: str
    title: str

@app.post("/api/create")
def create_documentary(req: DocRequest):
    # Dummy project ID for now
    project_id = int(uuid.uuid4().int % 10000)
    
    # Start the Celery pipeline
    task_id = start_pipeline(project_id, req.prompt)
    
    return {
        "message": "Documentary generation started!",
        "project_id": project_id,
        "task_id": task_id,
        "title": req.title
    }

@app.get("/health")
def health_check():
    return {"status": "Active and running"}
