import json
import logging
import os
import time
from typing import List, Dict, Any

from openai import OpenAI
import httpx
from celery import Celery, chain, chord
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from config import get_settings

settings = get_settings()
logger = logging.getLogger("documentary_maker")
logging.basicConfig(level=logging.INFO)

# Initialize Celery
celery_app = Celery('documentary_maker', broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(task_serializer='json', accept_content=['json'], result_serializer='json')

# Database Setup
Base = declarative_base()
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    status = Column(String, default="pending")

# --- Generators ---

class ScriptGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)
    
    def generate_script(self, prompt: str) -> Dict[str, Any]:
        logger.info(f"Generating script for: {prompt[:50]}")
        response = self.client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a documentary screenwriter."},
                {"role": "user", "content": f"Create a script about: {prompt}"}
            ]
        )
        return {
            "full_script": response.choices[0].message.content,
            "visual_style_guide": "Cinematic, 4k documentary style."
        }

class StoryboardGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)
    
    def generate_storyboard(self, script: str, style: str) -> List[Dict]:
        logger.info("Generating storyboard...")
        response = self.client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Output ONLY a JSON array."},
                {"role": "user", "content": f"Break script into 3 scenes. JSON keys: scene_number, voiceover_text, visual_prompt. Script: {script[:1000]}"}
            ]
        )
        try:
            return json.loads(response.choices[0].message.content)
        except:
            return [{"scene_number": 1, "voiceover_text": "Intro", "visual_prompt": "Wide shot."}]

# --- Celery Tasks ---

@celery_app.task(bind=True)
def generate_script_task(self, project_id: int, prompt: str):
    res = ScriptGenerator().generate_script(prompt)
    res["project_id"] = project_id
    return res

@celery_app.task(bind=True)
def generate_storyboard_task(self, script_result: Dict):
    scenes = StoryboardGenerator().generate_storyboard(script_result["full_script"], script_result["visual_style_guide"])
    for s in scenes:
        s["project_id"] = script_result["project_id"]
        s["style"] = script_result["visual_style_guide"]
    return scenes

@celery_app.task(bind=True)
def generate_audio_task(self, scene: Dict):
    logger.info(f"Generating audio for scene {scene['scene_number']}")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}"
    headers = {"xi-api-key": settings.elevenlabs_api_key}
    data = {"text": scene["voiceover_text"], "model_id": "eleven_monolingual_v1"}
    
    resp = httpx.post(url, json=data, headers=headers, timeout=120.0)
    path = f"/tmp/doc_{scene['project_id']}_audio_{scene['scene_number']}.mp3"
    with open(path, "wb") as f: f.write(resp.content)
    return {"audio": path}

@celery_app.task(bind=True)
def generate_video_task(self, scene: Dict):
    logger.info(f"Generating video for scene {scene['scene_number']}")
    # Runway API logic goes here (simplified for space)
    path = f"/tmp/doc_{scene['project_id']}_video_{scene['scene_number']}.mp4"
    return {"video": path}

@celery_app.task(bind=True)
def assemble_task(self, media_results: List, project_id: int):
    logger.info(f"Assembling video for project {project_id}")
    return {"status": "Complete", "final_video": f"/tmp/final_{project_id}.mp4"}

# --- Main Trigger ---
def start_pipeline(project_id: int, prompt: str):
    @celery_app.task
    def trigger_parallel(scenes: List[Dict]):
        tasks = [generate_audio_task.s(s) for s in scenes] + [generate_video_task.s(s) for s in scenes]
        return chord(tasks)(assemble_task.s(project_id=project_id))

    workflow = chain(generate_script_task.s(project_id, prompt), generate_storyboard_task.s(), trigger_parallel.s())
    return workflow.apply_async().id
