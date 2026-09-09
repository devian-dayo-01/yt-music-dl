#!/bin/bash
echo "YT Music 練習サイト（yt-dlp + Invidious自動）"
pip install -r requirements.txt
echo "Done! Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000"
