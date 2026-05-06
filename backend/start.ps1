# SQLBot backend dev launcher
# Pin HF/transformers to local cache to avoid 120s online check on startup.
# Usage:
#   conda activate sqlbot
#   .\start.ps1

$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:HF_HOME = "H:\opt\sqlbot\models\base\hub"

python -m uvicorn main:app --host 0.0.0.0 --port 8011 --workers 1
