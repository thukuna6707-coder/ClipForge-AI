# ClipForge AI

Turn long-form videos you own or are authorized to edit into 10–30 second vertical Shorts.

## Features
- Local speech transcription with faster-whisper.
- Transcript-aligned clip suggestions and explainable viral-potential estimates (not guaranteed views).
- Burned-in subtitles, 9:16 1080×1920 MP4 output, SRT downloads.
- Advanced duration, clip count, model, crop position, subtitle size/position, FPS and quality settings.

## YouTube links
A YouTube URL is stored as a reference only; a URL alone is not a downloadable source file. Upload the original video you own or have permission to edit, or use an official export/download route available to you. This app does not bypass platform access controls.

## Run
Requires Python 3.11+ and FFmpeg.
```bash
python -m venv .venv
# activate the environment, then:
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Open http://localhost:8000. First use downloads the selected Whisper model. Docker is also supported: `docker build -t clipforge . && docker run --rm -p 8000:8000 -v clipforge-data:/app/data clipforge`.

## Limitations
CPU processing may be slow. First release uses center/top/bottom crop rather than AI face tracking. Jobs are in memory and reset on server restart; add a persistent queue/database before multi-user production use. Only process content you have rights to use.