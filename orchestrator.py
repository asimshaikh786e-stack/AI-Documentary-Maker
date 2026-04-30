import json
import logging
import os
from typing import List, Dict

from openai import OpenAI
import httpx
from celery import Celery, chain, chord
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from gradio_client import Client
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips

from config import get_settings

settings = get_settings()
logger = logging.getLogger("documentary_maker")
logging.basicConfig(level=logging.INFO)

# Setup Celery
celery_app = Celery('documentary_maker', broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(task_serializer='json', accept_content=['json'], result_serializer='json')

# Setup Database
Base = declarative_base()
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    status = Column(String, default="pending")

# ==========================================
# 1. AI Generators (Script & Storyboard)
# ==========================================
class ScriptGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)
    
    def generate_script(self, prompt: str) -> Dict:
        logger.info(f"Generating script for: {prompt[:50]}")
        response = self.client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a documentary screenwriter."},
                {"role": "user", "content": f"Create a short documentary script about: {prompt}"}
            ]
        )
        return {"full_script": response.choices[0].message.content}

class StoryboardGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)
    
    def generate_storyboard(self, script: str) -> List[Dict]:
        logger.info("Generating storyboard...")
        response = self.client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Output ONLY a JSON array."},
                {"role": "user", "content": f"Break script into 2 short scenes. JSON keys: scene_number, voiceover_text, visual_prompt. Script: {script[:1000]}"}
            ]
        )
        try:
            return json.loads(response.choices[0].message.content)
        except:
            return [{"scene_number": 1, "voiceover_text": "Intro text.", "visual_prompt": "Cinematic shot."}]

# ==========================================
# 2. Celery Tasks (Pipeline Steps)
# ==========================================
@celery_app.task(bind=True)
def generate_script_task(self, project_id: int, prompt: str):
    res = ScriptGenerator().generate_script(prompt)
    res["project_id"] = project_id
    return res

@celery_app.task(bind=True)
def generate_storyboard_task(self, script_result: Dict):
    scenes = StoryboardGenerator().generate_storyboard(script_result["full_script"])
    for s in scenes:
        s["project_id"] = script_result["project_id"]
    return scenes

@celery_app.task(bind=True)
def generate_scene_media(self, scene: Dict):
    """Generates BOTH Audio and Video for a single scene"""
    project_id = scene['project_id']
    scene_num = scene['scene_number']
    os.makedirs(f"./temp_docs/{project_id}", exist_ok=True)
    
    # 1. Generate Audio (ElevenLabs)
    logger.info(f"Generating Audio for Scene {scene_num}")
    audio_path = f"./temp_docs/{project_id}/audio_{scene_num}.mp3"
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}"
        headers = {"xi-api-key": settings.elevenlabs_api_key}
        data = {"text": scene["voiceover_text"], "model_id": "eleven_monolingual_v1"}
        resp = httpx.post(url, json=data, headers=headers, timeout=120.0)
        with open(audio_path, "wb") as f: f.write(resp.content)
    except Exception as e:
        logger.error(f"Audio error: {e}")
        audio_path = None

    # 2. Generate Video (Gradio Wan2.1)
    logger.info(f"Generating Video for Scene {scene_num} using Wan2.1")
    video_path = None
    try:
        client = Client("Wan-Video/Wan2.1-T2V-1.3B") 
        result = client.predict(
            prompt=scene["visual_prompt"],
            negative_prompt="low quality, blurry, distorted",
            num_inference_steps=25,
            guidance_scale=6.0,
            api_name="/generate_video"
        )
        video_path = result
    except Exception as e:
        logger.error(f"Video error: {e}")

    return {
        "scene_number": scene_num,
        "audio_path": audio_path,
        "video_path": video_path
    }

@celery_app.task(bind=True)
def assemble_task(self, scene_results: List[Dict], project_id: int):
    """Uses MoviePy to sync audio to video and concatenate all scenes"""
    logger.info(f"Assembling Documentary for Project {project_id}")
    
    final_output = f"./temp_docs/{project_id}/FINAL_DOCUMENTARY.mp4"
    clips_to_concat = []
    
    # Sort scenes by number
    scene_results.sort(key=lambda x: x['scene_number'])
    
    for res in scene_results:
        vid_path = res.get('video_path')
        aud_path = res.get('audio_path')
        
        if vid_path and os.path.exists(vid_path):
            video_clip = VideoFileClip(vid_path)
            
            if aud_path and os.path.exists(aud_path):
                audio_clip = AudioFileClip(aud_path)
                video_clip = video_clip.set_audio(audio_clip)
                
            clips_to_concat.append(video_clip)
            
    if clips_to_concat:
        final_video = concatenate_videoclips(clips_to_concat, method="compose")
        final_video.write_videofile(final_output, fps=24)
        logger.info(f"Success! Documentary is ready: {final_output}")
    else:
        logger.error("No valid video clips found to assemble.")
        
    return {"status": "Complete", "final_video": final_output}

# ==========================================
# 3. Main Workflow Trigger
# ==========================================
def start_pipeline(project_id: int, prompt: str):
    @celery_app.task
    def trigger_parallel(scenes: List[Dict]):
        tasks = [generate_scene_media.s(s) for s in scenes]
        return chord(tasks)(assemble_task.s(project_id=project_id))

    workflow = chain(
        generate_script_task.s(project_id, prompt), 
        generate_storyboard_task.s(), 
        trigger_parallel.s()
    )
    return workflow.apply_async().id
