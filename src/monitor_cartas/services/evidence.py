import hashlib
import json
from datetime import datetime
from pathlib import Path


def save_json_evidence(
    evidence_dir: Path, site: str, source_id: str, when: datetime, payload: dict
) -> tuple[Path, str]:
    day = when.strftime("%Y-%m-%d")
    target_dir = evidence_dir / site / day / str(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    path = target_dir / f"{when.strftime('%H%M%S')}.json"
    path.write_text(content)
    return path, content_hash
