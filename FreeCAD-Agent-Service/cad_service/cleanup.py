"""Delete only a verified, direct child of an explicitly supplied service root."""
from pathlib import Path
import shutil
import uuid


def remove_child(root, name, require_uuid=False):
    if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name or Path(name).name != name:
        raise ValueError('Cleanup requires a direct child name')
    if require_uuid and str(uuid.UUID(name)) != name:
        raise ValueError('Cleanup requires a canonical job UUID')
    root = Path(root).resolve()
    target = root / name
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise ValueError('Refusing to follow a cleanup directory link')
    # Verify the final absolute target immediately before recursive deletion.
    resolved = target.resolve()
    if resolved.parent != root or resolved == root:
        raise ValueError('Cleanup target escapes its service root')
    if not target.exists():
        return
    if not target.is_dir():
        raise ValueError('Cleanup target is not a directory')
    shutil.rmtree(resolved)


def clean_job(queue, artifacts, homes, job_id):
    if not queue.cleanup_allowed(job_id):
        return
    remove_child(homes, job_id, require_uuid=True)
    remove_child(artifacts, job_id, require_uuid=True)
    # Mark only after both deletions succeed; repeating after a crash is safe.
    queue.complete_cleanup(job_id)


def clean_pending(queue, artifacts, homes, include_interrupted=False):
    for job_id in queue.pending_cleanup(include_interrupted):
        clean_job(queue, artifacts, homes, job_id)
